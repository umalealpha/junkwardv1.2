"""healthcare/permissions.py — access gate for the Vendor Onboarding portal.

CFO/EXCO directive 2026-06-04: only Ankete may access the vendor onboarding
portal. Login uses the existing omni Microsoft SSO; this class restricts the
endpoints to an allowlist of @alphadirect.co.bw local-parts (mirrors the
pattern in core/hris_access.py). Override via settings.VENDOR_ONBOARDING_
ALLOWED_LOCALPARTS (comma-separated or set).
"""
from __future__ import annotations

from django.conf import settings
from rest_framework.permissions import BasePermission

# Local-parts (the bit before @alphadirect.co.bw). Microsoft sign-in may present
# variants (ankete, anketem, etc.) — keep the allowlist explicit and broadenable
# via settings without a code change.
_DEFAULT_ALLOWED = {"ankete"}
_ALLOWED_DOMAIN = "alphadirect.co.bw"


def _allowed_local_parts() -> set[str]:
    raw = getattr(settings, "VENDOR_ONBOARDING_ALLOWED_LOCALPARTS", None)
    if raw is None:
        return set(_DEFAULT_ALLOWED)
    if isinstance(raw, str):
        return {p.strip().lower() for p in raw.split(",") if p.strip()}
    return {str(p).strip().lower() for p in raw}


class IsVendorOnboarder(BasePermission):
    message = "Access to the vendor onboarding portal is restricted to authorised users."

    def has_permission(self, request, view) -> bool:
        u = getattr(request, "user", None)
        if not (u and u.is_authenticated):
            return False
        if getattr(u, "is_superuser", False):
            return True
        email = (getattr(u, "email", "") or getattr(u, "username", "") or "").strip().lower()
        if "@" not in email:
            return False
        local, _, domain = email.partition("@")
        return domain == _ALLOWED_DOMAIN and local in _allowed_local_parts()


# ---------------------------------------------------------------------------
# ADH → AFA load file.
#
# The load file is every ADH member's name, ID number, date of birth and bank
# account. Preview and release are gated HERE, server-side, returning 403 —
# hiding the button in the UI is not a control (checklist H5).
# ---------------------------------------------------------------------------

# Keetile Mokhendo (kmokhendo) added by the CFO on 2026-09-13: he owns the ADH
# health settlement feed and could not open the screens that run it.
_AFA_DEFAULT_ALLOWED = {
    "rtonkope", "mtlagae", "lkeotlhoboge", "pganesharajah", "kmokhendo",
}


def _afa_allowed_local_parts() -> set[str]:
    raw = getattr(settings, "AFA_LOADFILE_ALLOWED_LOCALPARTS", None)
    if raw is None:
        return set(_AFA_DEFAULT_ALLOWED)
    if isinstance(raw, str):
        return {p.strip().lower() for p in raw.split(",") if p.strip()}
    return {str(p).strip().lower() for p in raw}


class IsAfaLoadFileOperator(BasePermission):
    message = "The AFA load file is restricted to authorised healthcare staff."

    def has_permission(self, request, view) -> bool:
        u = getattr(request, "user", None)
        if not (u and u.is_authenticated):
            return False
        if getattr(u, "is_superuser", False):
            return True
        email = (getattr(u, "email", "") or getattr(u, "username", "") or "").strip().lower()
        if "@" not in email:
            return False
        local, _, domain = email.partition("@")
        return domain == _ALLOWED_DOMAIN and local in _afa_allowed_local_parts()


# ---------------------------------------------------------------------------
# ADH Service Provider registry (network readiness tracker).
#
# The registry holds provider CONTACT details (email/phone) — internal only,
# never the public directory. Import/QC-edit/export are gated server-side.
# ---------------------------------------------------------------------------

# Meduduetso Tlagae (registry owner) asked for the rest of the ADH Health team
# on 2026-09-08 so they can keep the provider network up to date: Keneilwe Jane,
# Amantle Thake and Onkgolotse Sebetlela. Ritah Tonkope and Loapi Keotlhoboge
# were already on the list.
_SP_DEFAULT_ALLOWED = {
    "mtlagae", "rtonkope", "lkeotlhoboge", "pganesharajah",
    "kjane", "athake", "osebetlela",
}


def _sp_allowed_local_parts() -> set[str]:
    raw = getattr(settings, "SERVICE_PROVIDER_ALLOWED_LOCALPARTS", None)
    if raw is None:
        return set(_SP_DEFAULT_ALLOWED)
    if isinstance(raw, str):
        return {p.strip().lower() for p in raw.split(",") if p.strip()}
    return {str(p).strip().lower() for p in raw}


class IsServiceProviderManager(BasePermission):
    message = "The service-provider registry is restricted to authorised ADH staff."

    def has_permission(self, request, view) -> bool:
        u = getattr(request, "user", None)
        if not (u and u.is_authenticated):
            return False
        if getattr(u, "is_superuser", False):
            return True
        email = (getattr(u, "email", "") or getattr(u, "username", "") or "").strip().lower()
        if "@" not in email:
            return False
        local, _, domain = email.partition("@")
        return domain == _ALLOWED_DOMAIN and local in _sp_allowed_local_parts()
