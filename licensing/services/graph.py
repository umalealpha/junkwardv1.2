"""Microsoft Graph client (service-principal / client-credentials).

Reads enabled member users with their assignedLicenses and signInActivity
from the beta endpoint, paginates through @odata.nextLink, then applies
two filters:

  1. Has at least one assigned license.
  2. Not a shared/forward mailbox — heuristic on displayName + UPN local
     (mirrors the heuristic the CFO office signed off on 2026-05-28).

Returns a list of dicts ready for upsert into `M365ActiveUser`.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable

import msal
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com"

# Local-part keywords (regex) that mark a UPN as a shared/forward inbox.
_SHARED_LOCAL_RE = re.compile(
    r"(claim|health|helpdesk|helpline|case|preauth|provider|monitor|docs|talent|"
    r"staff|queries|conditions|capture|correction|ceo|board|exco|finance|ops|"
    r"sales|marketing|billing|payroll|accounts|archive|vault|reception|info|"
    r"support|contact|noreply|no-reply|news|omni|kpi|erp|complaints|returns|"
    r"leasing|recoveries|jobs|careers|procurement|abuse|postmaster|systems|"
    r"tickets|services|dept|team|group|hr|admin|administration|gfspamir|all|"
    r"alphadirect|alphaadmin|customerservice|webmaster|webteam|frontdesk|"
    r"legal|compliance|newsletter|alerts|monitoring|mailroom)"
)
_ALLCAPS_RE = re.compile(r"^[A-Z0-9 \-.()&]+$")


class GraphError(RuntimeError):
    pass


def _token() -> str:
    tenant = getattr(settings, "M365_TENANT_ID", "")
    client_id = getattr(settings, "M365_CLIENT_ID", "")
    client_secret = getattr(settings, "M365_CLIENT_SECRET", "")
    if not (tenant and client_id and client_secret):
        raise GraphError(
            "Missing Graph credentials. Set M365_TENANT_ID / M365_CLIENT_ID / "
            "M365_CLIENT_SECRET in /etc/alpha-finance/.env."
        )
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=client_secret,
    )
    res = app.acquire_token_for_client(scopes=[f"{GRAPH}/.default"])
    if "access_token" not in res:
        raise GraphError(f"Graph auth failed: {res.get('error_description') or res}")
    return res["access_token"]


def directory_name_for_email(email: str) -> str | None:
    """Resolve a real 'Given Surname' from the M365 directory for one email/UPN.

    Used to show Alpha Nexus testers who signed in with a company address by
    their real name (the directory is the source of truth, already synced with
    omni). Returns None on anything unexpected — the caller falls back to a
    tidied local-part, so this must never raise into a registration path.
    """
    email = (email or "").strip()
    if not email or "@" not in email:
        return None
    try:
        token = _token()
        r = requests.get(
            f"{GRAPH}/v1.0/users/{email}",
            headers={"Authorization": f"Bearer {token}"},
            params={"$select": "givenName,surname,displayName"},
            timeout=15,
        )
        if r.status_code != 200:
            return None  # 404 = no such mailbox (e.g. a typo'd self-registration)
        u = r.json()
        full = f'{u.get("givenName") or ""} {u.get("surname") or ""}'.strip()
        return full or (u.get("displayName") or "").strip() or None
    except Exception:
        logger.warning("directory_name_for_email failed for %s", email, exc_info=True)
        return None


def _is_shared(display_name: str, upn: str) -> bool:
    dn = (display_name or "").strip()
    if not dn:
        return True
    # ALL CAPS display name (e.g. "ALPHA DIRECT RECOVERIES") => shared
    if len(dn) > 1 and _ALLCAPS_RE.match(dn):
        return True
    # Single-word display name (e.g. "Talent", "Staff", "Conditions") => shared
    if " " not in dn:
        return True
    local = (upn or "").split("@", 1)[0].lower()
    if _SHARED_LOCAL_RE.search(local):
        return True
    return False


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    # Graph returns "2026-05-27T05:53:00Z"; normalise to aware UTC.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_active_licensed_humans(cutoff: datetime) -> list[dict]:
    """Return list of dicts for active humans whose interactive sign-in
    is on/after `cutoff`. `cutoff` MUST be timezone-aware UTC."""
    token = _token()
    headers = {"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"}
    url = (
        f"{GRAPH}/beta/users"
        "?$filter=accountEnabled eq true and userType eq 'Member'"
        "&$select=id,displayName,userPrincipalName,mail,jobTitle,department,"
        "assignedLicenses,accountEnabled,userType,signInActivity"
        "&$top=999"
    )
    out: list[dict] = []
    total_seen = 0
    while url:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code >= 400:
            raise GraphError(f"Graph error {r.status_code}: {r.text[:500]}")
        payload = r.json()
        for u in payload.get("value", []):
            total_seen += 1
            if not (u.get("assignedLicenses") or []):
                continue
            if _is_shared(u.get("displayName", ""), u.get("userPrincipalName", "")):
                continue
            last = _parse_dt((u.get("signInActivity") or {}).get("lastSignInDateTime"))
            if not last or last < cutoff:
                continue
            out.append({
                "object_id": u["id"],
                "display_name": u.get("displayName") or "",
                "user_principal_name": u.get("userPrincipalName") or "",
                "email": (u.get("mail") or u.get("userPrincipalName") or "").lower(),
                "job_title": u.get("jobTitle") or "",
                "department": u.get("department") or "",
                "license_count": len(u.get("assignedLicenses") or []),
                "last_interactive_signin_at": last,
            })
        url = payload.get("@odata.nextLink")
    logger.info("Graph users scanned=%d active_humans=%d", total_seen, len(out))
    return out, total_seen


def filter_humans(rows: Iterable[dict]) -> list[dict]:
    """Helper for tests / offline data — applies the same heuristic."""
    return [r for r in rows if not _is_shared(r.get("display_name", ""), r.get("user_principal_name", ""))]
