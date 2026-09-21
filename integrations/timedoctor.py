"""
integrations/timedoctor.py

Read-only client for the Time Doctor v2 API — pulls the company's workforce
time-tracking + productivity data into omni on a daily schedule.

Source contract (Time Doctor API v1.0, https://api2.timedoctor.com):
  Auth   : a long-lived JWT bearer token minted ONCE via POST /api/1.0/login
           (email + password + totpCode). The token — NOT the password — is
           stored as the TIMEDOCTOR_TOKEN secret. Every call also needs the
           company id as ?company=<id>.
  Used endpoints (all GET, all confirmed live 2026-06-16):
    /api/1.0/users?company=&detail=info        → roster (name, email, role,
                                                  lastSeen, lastTrack, timezone)
    /api/1.0/activity/worklog?company=&from=&to → per-user [{start,time(s),mode}]
    /api/1.0/activity/timeuse?company=&from=&to → per-user [{time(s),score,…}]
                                                  score 0=unrated 1-2=unproductive
                                                  3-4=productive
    /api/1.0/projects?company=                  → projects
    /api/1.0/tasks?company=                     → tasks

PRIVACY (AD-POL-AI-GOV-001): the timeuse rows carry window/app titles that can
contain customer PII (e.g. a policy/customer name in an email subject). This
module NEVER persists or surfaces raw titles — it aggregates timeuse to
productivity SECONDS per score bucket per user and throws the titles away.
omni + the daily email only ever see per-user hours + productivity % +
attendance. No employee keystroke/window content leaves the box.

Settings (decouple; default '' / unset → client disabled, cron exits SKIPPED):
  TIMEDOCTOR_API_BASE        default https://api2.timedoctor.com
  TIMEDOCTOR_TOKEN           JWT minted via /login (NOT the password)
  TIMEDOCTOR_COMPANY_ID      e.g. XnInT13-PAAEpEkJ  (Alpha Direct)
  TIMEDOCTOR_TIMEOUT_SECONDS default 45
  TIMEDOCTOR_USER_BATCH      default 10 — people per worklog/timeuse request;
                             the whole company in one call is what their API
                             truncates (see _activity)
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone as _timezone
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings

from integrations.td_matching import canonical_identity

log = logging.getLogger(__name__)


def jwt_expiry(token: str) -> Optional[datetime]:
    """Return the UTC expiry (`exp` claim) encoded inside a Time Doctor JWT, or
    None if the token is missing / malformed / carries no exp.

    The signature is NOT verified — the API itself is the authority on whether a
    token is still valid. We only read the token's self-reported expiry so the
    renewal cron can remind people before it lapses (Time Doctor tokens live
    ~6 months and there is no non-expiring company key — vendor confirmed
    2026-07-14). Never raises."""
    if not token or token.count('.') < 2:
        return None
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += '=' * (-len(payload_b64) % 4)          # restore base64url padding
        data = json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
        exp = data.get('exp')
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=_timezone.utc)
    except Exception:    # noqa: BLE001
        return None

# Productivity score buckets (Time Doctor `score` on each timeuse row).
PRODUCTIVE_SCORES   = {3, 4}
UNPRODUCTIVE_SCORES = {1, 2}
# score 0 == unrated/neutral


class TimeDoctorError(Exception):
    """Raised on a non-2xx API response or a missing-config call."""


def _token_from_vault() -> str:
    """Read the Time Doctor JWT from the CFO Secrets Vault (entry named
    'TIMEDOCTOR_TOKEN') when the TIMEDOCTOR_TOKEN env var is unset. Lets the CFO
    paste / rotate the token in-browser (Fernet-encrypted at rest, audit-logged)
    instead of editing the server .env. Never raises — a miss yields '' and the
    client simply stays disabled (cron exits SKIPPED). CFO 2026-06-16."""
    try:
        from core.models import VaultSecret
        s = VaultSecret.objects.filter(name__iexact='TIMEDOCTOR_TOKEN').first()
        return (s.reveal() if s else '') or ''
    except Exception:    # noqa: BLE001
        return ''


@dataclass
class TimeDoctorClient:
    token:      str = ''
    company_id: str = ''
    base:       str = ''
    timeout:    int = 45
    # Set after a header-auth 401: this API deployment only accepts ?token=.
    _query_token: bool = field(default=False, repr=False)

    @classmethod
    def from_settings(cls) -> 'TimeDoctorClient':
        # Env wins; else fall back to the CFO Secrets Vault so the JWT can be
        # pasted / rotated in-browser rather than in the server .env (CFO 2026-06-16).
        token = (getattr(settings, 'TIMEDOCTOR_TOKEN', '') or '') or _token_from_vault()
        return cls(
            token=token,
            company_id=getattr(settings, 'TIMEDOCTOR_COMPANY_ID', '') or '',
            base=(getattr(settings, 'TIMEDOCTOR_API_BASE', '') or 'https://api2.timedoctor.com').rstrip('/'),
            timeout=int(getattr(settings, 'TIMEDOCTOR_TIMEOUT_SECONDS', 45) or 45),
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.company_id)

    # -- low level -----------------------------------------------------------
    # ── response parsing ────────────────────────────────────────────────────
    # Time Doctor occasionally returns a body that is too large to be valid JSON —
    # a 14 MB payload cut off mid-string. Live on 2026-08-03 this crashed the 07:00
    # manager report SIX times (against four successful sends), and because the crash
    # happened inside cron, nobody was told: managers simply got no email that day.
    # So: parse defensively, retry with a smaller page, and if it still fails raise a
    # message that says what actually went wrong.
    MAX_PARSE_ATTEMPTS = 3

    def _parse(self, r, path: str, params: Dict[str, Any], attempt: int):
        try:
            return r.json().get('data')
        except ValueError as exc:      # JSONDecodeError subclasses ValueError
            size = len(r.content or b'')
            limit = params.get('limit')
            log.warning('Time Doctor sent %s bytes for %s that would not parse (attempt %s/%s): %s',
                        size, path, attempt, self.MAX_PARSE_ATTEMPTS, exc)
            if attempt >= self.MAX_PARSE_ATTEMPTS:
                raise TimeDoctorError(
                    f'Time Doctor returned {size:,} bytes for {path} that could not be read as '
                    f'JSON, after {attempt} attempts. This is their API truncating a large '
                    f'response, not a token problem.') from None
            # Halve the page size and try again — a smaller response is the only thing
            # that reliably fixes a truncated one.
            smaller = dict(params)
            smaller['limit'] = max(50, int(limit) // 2) if limit else 250
            return self._get(path, smaller, _attempt=attempt + 1)

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None, _attempt: int = 1) -> Any:
        if not self.configured:
            raise TimeDoctorError('TIMEDOCTOR_TOKEN / TIMEDOCTOR_COMPANY_ID not set.')
        params = dict(params or {})
        params.setdefault('company', self.company_id)
        url = f'{self.base}{path}'
        # Prefer the Authorization header — a ?token= query param ends up in
        # proxy/access logs (Fable review 2026-07-14). Fall back to the query
        # param once if this deployment rejects header auth, and remember.
        if not self._query_token:
            r = requests.get(url, params=params, timeout=self.timeout,
                             headers={'Authorization': f'JWT {self.token}'})
            if r.status_code in (401, 403):
                log.warning('Time Doctor rejected header auth (%s) — falling back to ?token=', r.status_code)
                self._query_token = True
            else:
                if not r.ok:
                    raise TimeDoctorError(f'{r.status_code} from {path}: {r.text[:200]}')
                return self._parse(r, path, params, _attempt)
        r = requests.get(url, params={**params, 'token': self.token}, timeout=self.timeout)
        if r.status_code == 401:
            raise TimeDoctorError('401 from Time Doctor — token expired or invalid. Re-mint TIMEDOCTOR_TOKEN.')
        if not r.ok:
            raise TimeDoctorError(f'{r.status_code} from {path}: {r.text[:200]}')
        return self._parse(r, path, params, _attempt)

    # -- endpoints -----------------------------------------------------------
    def users(self) -> List[dict]:
        data = self._get('/api/1.0/users', {'detail': 'info', 'limit': 500})
        return data or []

    @staticmethod
    def _fmt_bound(d) -> str:
        """A window bound for from/to: dates as YYYY-MM-DD, datetimes as full
        UTC ISO — so callers can request an exact Botswana (UTC+2) day instead
        of the UTC day (evening work was leaking into the next day)."""
        if isinstance(d, datetime):
            return d.strftime('%Y-%m-%dT%H:%M:%SZ')
        return d.isoformat()

    # ── batching the per-user activity pulls ────────────────────────────────
    # Asking for the whole company in one request is what produces the payload
    # their API cannot return intact: on 2026-09-20 timeuse came back as
    # 11,887,954 bytes of truncated JSON three attempts running and HELD the
    # 07:00 Morning Brief for the whole company. Halving `limit` (see _parse)
    # does not help — the body is already cut off in transit, and the retry
    # just asks for the same enormous window again.
    #
    # So ask for fewer PEOPLE per request instead. For worklog a batch of ten
    # keeps each response comfortably inside what Time Doctor returns whole;
    # timeuse goes one person at a time (see `timeuse` below — its rows carry
    # no userId, so position is the only attribution there is). The pull is a
    # daily cron where the extra round-trips cost nothing.
    USER_BATCH_SIZE = 10

    def _batch_size(self) -> int:
        try:
            n = int(getattr(settings, 'TIMEDOCTOR_USER_BATCH', self.USER_BATCH_SIZE)
                    or self.USER_BATCH_SIZE)
        except (TypeError, ValueError):
            n = self.USER_BATCH_SIZE
        return max(1, n)

    def _one_per_user(self, path: str, base: dict, ids: List[str]) -> List[list]:
        """One request per user, one bucket appended per user, in id order.

        Attribution needs no index at all here: each response is the answer to
        a question about exactly one person, so the bucket that lands at
        position i belongs to ids[i] BY CONSTRUCTION, not because Time Doctor
        happened to preserve the order we asked in. A user with nothing to
        report still gets an empty bucket, so the list is never short.
        """
        out: List[list] = []
        for uid in ids:
            got = self._get(path, {**base, 'user': uid}) or []
            # One user asked for, one bucket expected; keep the shape stable so
            # the positional mapping still holds downstream.
            out.append(got[0] if len(got) == 1 else (got or []))
        return out

    def _activity(self, path: str, day_from, day_to, user_ids, limit: int,
                  per_user: bool = False) -> List[list]:
        """One activity pull, split by people, concatenated in REQUEST ORDER.

        `per_user=True` asks for exactly one person per request. That is the
        only way to be certain of attribution for an endpoint whose rows carry
        no userId (timeuse): the alternative — a batch of ten, mapped by index
        — is correct only while Time Doctor returns the ten buckets in the
        order asked, which nothing in their API guarantees and no length check
        can detect. A shuffle inside a full-length batch would credit one
        person's productive hours to a colleague, silently, on the data that
        docks pay.

        With `per_user=False` (worklog) the rows carry `userId`, so the order
        of the response is irrelevant and batching is free.
        """
        base = {'from': self._fmt_bound(day_from), 'to': self._fmt_bound(day_to),
                'limit': limit}
        if not user_ids:
            # No id list: a single call is all we can do (and `aggregate` falls
            # back to roster order). Unchanged behaviour.
            return self._get(path, dict(base)) or []

        ids = [str(u) for u in user_ids]
        if per_user:
            return self._one_per_user(path, base, ids)

        size = self._batch_size()
        out: List[list] = []
        for start in range(0, len(ids), size):
            chunk = ids[start:start + size]
            got = self._get(path, {**base, 'user': ','.join(chunk)}) or []
            if len(chunk) > 1 and len(got) != len(chunk):
                log.warning(
                    '%s returned %s buckets for %s users — re-fetching this batch '
                    'one user at a time.', path, len(got), len(chunk))
                got = self._one_per_user(path, base, chunk)
            out.extend(got)
        return out

    def worklog(self, day_from, day_to, user_ids=None) -> List[list]:
        # Time Doctor's worklog returns ONLY the token owner's rows unless the
        # caller passes user=<comma-joined ids>. Pass every user's id so the
        # company-wide daily pull covers the whole team, not just the owner.
        #
        # Batched: worklog rows carry `userId`, so who a row belongs to is read
        # off the row itself and the order of the response does not matter.
        return self._activity('/api/1.0/activity/worklog', day_from, day_to,
                              user_ids, limit=50000)

    def timeuse(self, day_from, day_to, user_ids=None) -> List[list]:
        # ONE USER PER REQUEST. timeuse rows carry no userId whatsoever —
        # aggregate(), workforce_pulse.focus_stats() and td_integrity all
        # attribute each bucket by its POSITION against the ordered id list.
        # Asking about one person at a time makes that position a fact about
        # the request rather than a hope about the response.
        return self._activity('/api/1.0/activity/timeuse', day_from, day_to,
                              user_ids, limit=100000, per_user=True)

    def files(self, day_from, day_to, user_ids=None) -> List[dict]:
        # Screenshot metadata (keys/movements/clicks + image fingerprints per
        # screenshot). Used by the frozen-screen fraud detector. Like worklog,
        # pass the user ids so the pull covers the whole team, not just the owner.
        params = {'from': self._fmt_bound(day_from), 'to': self._fmt_bound(day_to), 'limit': 100000}
        if user_ids:
            params['user'] = ','.join(str(u) for u in user_ids)
        return self._get('/api/1.0/files', params) or []

    def projects(self) -> List[dict]:
        return self._get('/api/1.0/projects', {'limit': 500}) or []

    def tasks(self) -> List[dict]:
        return self._get('/api/1.0/tasks', {'limit': 500}) or []


# ---------------------------------------------------------------------------
# Aggregation — turns raw API payloads into a privacy-safe daily snapshot.
# Pure functions so they unit-test against captured sample JSON with no network.
# ---------------------------------------------------------------------------

def _flatten(per_user_arrays: List[Any]) -> List[dict]:
    """Worklog/timeuse return data as a list of per-user arrays of row dicts.
    Flatten to one row list. Tolerant of already-flat input."""
    rows: List[dict] = []
    for bucket in (per_user_arrays or []):
        if isinstance(bucket, list):
            rows.extend(r for r in bucket if isinstance(r, dict))
        elif isinstance(bucket, dict):
            rows.append(bucket)
    return rows


def aggregate(users: List[dict], worklog: List[Any], timeuse: List[Any],
              projects: List[dict], tasks: List[dict], *, as_of: date,
              td_user_ids: Optional[List[Any]] = None) -> dict:
    """Build the privacy-safe daily aggregate. Raw window titles are dropped.

    `td_user_ids` is the ordered list of Time Doctor user ids passed to the
    worklog/timeuse API (the `user=` param). Time Doctor returns timeuse as one
    array PER USER in that request order, and — unlike worklog — the rows carry
    NO userId. Without the ordered ids we cannot attribute any productivity time
    to a person, so every productive/unproductive second was silently dropped and
    productive hours read as zero (CFO 2026-07-16). Defaults to the roster order
    when not supplied, so older callers are fixed too."""
    ordered_ids = (list(td_user_ids) if td_user_ids is not None
                   else [u.get('id') for u in (users or []) if u.get('id')])
    by_id: Dict[str, dict] = {}
    for u in users:
        uid = u.get('id')
        if not uid:
            continue
        by_id[uid] = {
            'user_id':   uid,
            'name':      u.get('name') or '',
            'email':     u.get('email') or '',
            'role':      u.get('role') or '',
            'timezone':  u.get('timezone') or '',
            'last_seen':  u.get('lastSeen'),
            'last_track': u.get('lastTrack'),
            'tracked_seconds':      0,
            'manual_seconds':       0,
            'productive_seconds':   0,
            'unproductive_seconds': 0,
            'neutral_seconds':      0,
        }

    def _row_user(row: dict) -> Optional[dict]:
        uid = row.get('userId')
        if uid is None:
            return None
        return by_id.setdefault(uid, {
            'user_id': uid, 'name': '', 'email': '', 'role': '', 'timezone': '',
            'last_seen': None, 'last_track': None, 'tracked_seconds': 0,
            'manual_seconds': 0,
            'productive_seconds': 0, 'unproductive_seconds': 0, 'neutral_seconds': 0,
        })

    # worklog → tracked seconds per user.
    #
    # Time Doctor tags every worklog row with a `mode`. 'manual' rows are time a
    # person typed in afterwards, not time a device observed. Mixing the two
    # inflates the clock and cannot be verified: on 28 July the CFO's desktop
    # carried 9.02 h of observed computer time plus 4.70 h of manual entries, and
    # the report published the 13.72 h total. Keep them in separate buckets so the
    # headline figure is the observed one and manual time can still be shown, and
    # owned, on its own line (CFO 2026-07-29).
    for row in _flatten(worklog):
        rec = _row_user(row)
        if rec is None:
            continue
        secs = int(row.get('time') or 0)
        if str(row.get('mode') or '').strip().lower() == 'manual':
            rec['manual_seconds'] = (rec.get('manual_seconds') or 0) + secs
        else:
            rec['tracked_seconds'] += secs

    # timeuse → productivity buckets per user (titles discarded)
    # NOTE timeuse rows have no userId on each row; they sit inside a per-user
    # bucket. Walk buckets so we can attribute scores to the right user.
    for idx, bucket in enumerate(timeuse or []):
        rows = bucket if isinstance(bucket, list) else [bucket]
        # Attribute to the user whose worklog/users index aligns; Time Doctor
        # returns timeuse buckets in the same user order as the request. When a
        # row carries userId use it, else fall back to the bucket's first userId,
        # else to the request-order id (timeuse rows carry NO userId at all in the
        # current API, so this index fallback is what actually attributes them).
        bucket_uid = None
        for row in rows:
            if isinstance(row, dict) and row.get('userId'):
                bucket_uid = row['userId']; break
        if bucket_uid is None and idx < len(ordered_ids):
            bucket_uid = ordered_ids[idx]
        for row in rows:
            if not isinstance(row, dict):
                continue
            uid = row.get('userId') or bucket_uid
            if not uid:
                continue
            rec = by_id.setdefault(uid, {
                'user_id': uid, 'name': '', 'email': '', 'role': '', 'timezone': '',
                'last_seen': None, 'last_track': None, 'tracked_seconds': 0,
                'manual_seconds': 0,
                'productive_seconds': 0, 'unproductive_seconds': 0, 'neutral_seconds': 0,
            })
            secs = int(row.get('time') or 0)
            score = row.get('score')
            if score in PRODUCTIVE_SCORES:
                rec['productive_seconds'] += secs
            elif score in UNPRODUCTIVE_SCORES:
                rec['unproductive_seconds'] += secs
            else:
                rec['neutral_seconds'] += secs

    # Fold a person's multiple Time Doctor machines onto one identity BEFORE
    # computing derived %s, so e.g. the CFO's laptop + desktop show as one row
    # (CFO 2026-07-22).
    #
    # We take the BUSIEST MACHINE, not the sum (CFO 2026-07-29). Summing was
    # wrong and visibly so: a person running two machines through the same
    # working day had both clocks counted, so the CFO's 27 July read 21.64 h in
    # a 24-hour day and went to all 79 staff as "yesterday's hero". His real day
    # was 9-10 h — which Time Doctor's own longest-unbroken-session figure
    # independently put at 9 h 14 m. Time Doctor bills per device and daily
    # aggregates carry no intervals, so concurrent wall-clock cannot be
    # de-overlapped; the only honest figure available is the machine the person
    # actually worked the most on.
    #
    # The dominant machine's record is taken WHOLE — tracked, productive,
    # unproductive and neutral seconds together — because mixing one machine's
    # tracked clock with another's productivity split would corrupt every derived
    # percentage. `machine_count` records how many machines were seen so a report
    # can say so out loud.
    collapsed: Dict[str, dict] = {}
    for rec in by_id.values():
        c_uid, c_name = canonical_identity(rec.get('user_id'), rec.get('name'))
        key = str(c_uid) if c_uid is not None else (rec.get('email') or c_name or '')
        tgt = collapsed.get(key)
        if tgt is None:
            rec['user_id'], rec['name'] = c_uid, c_name
            rec['machine_count'] = 1
            collapsed[key] = rec
            continue

        seen = (tgt.get('machine_count') or 1) + 1
        if (rec.get('tracked_seconds') or 0) > (tgt.get('tracked_seconds') or 0):
            # This machine is the busier one — it becomes the reported record.
            rec['user_id'], rec['name'] = c_uid, c_name
            rec['role'] = rec.get('role') or tgt.get('role')
            rec['email'] = rec.get('email') or tgt.get('email')
            for f in ('last_seen', 'last_track'):   # keep the most recent
                if tgt.get(f) and (not rec.get(f) or str(tgt[f]) > str(rec[f])):
                    rec[f] = tgt[f]
            rec['machine_count'] = seen
            collapsed[key] = rec
        else:
            tgt['role'] = tgt.get('role') or rec.get('role')
            tgt['email'] = tgt.get('email') or rec.get('email')
            for f in ('last_seen', 'last_track'):
                if rec.get(f) and (not tgt.get(f) or str(rec[f]) > str(tgt[f])):
                    tgt[f] = rec[f]
            tgt['machine_count'] = seen
    by_id = collapsed

    members = []
    for rec in by_id.values():
        rated = rec['productive_seconds'] + rec['unproductive_seconds'] + rec['neutral_seconds']
        prod_base = rec['productive_seconds'] + rec['unproductive_seconds']
        rec['hours_tracked']     = round(rec['tracked_seconds'] / 3600, 2)
        rec['hours_manual']      = round((rec.get('manual_seconds') or 0) / 3600, 2)
        rec['productive_pct']    = round(100 * rec['productive_seconds'] / prod_base, 1) if prod_base else None
        rec['unproductive_pct']  = round(100 * rec['unproductive_seconds'] / prod_base, 1) if prod_base else None
        # Productive HOURS = tracked hours × (productive share of categorised time).
        # We scale the tracked hours by the productive share rather than converting
        # productive seconds directly: timeuse (app + website) slightly exceeds the
        # worklog clock, so raw productive seconds could read HIGHER than the hours
        # actually on the clock. Scaling keeps productive ≤ tracked (CFO 2026-07-16).
        rec['productive_hours']  = round((rec['tracked_seconds'] / 3600) * (rec['productive_seconds'] / rated), 2) if rated else None
        rec['tracked_today']     = rec['tracked_seconds'] > 0
        members.append(rec)

    # Rank on PRODUCTIVE hours, not the raw clock (CFO 2026-07-29). Time at a
    # desk is not the number anyone should be ranked on, and it was the raw clock
    # that produced the impossible figures.
    members.sort(key=lambda m: ((m.get('productive_hours') or 0), m['tracked_seconds']),
                 reverse=True)

    total_tracked = sum(m['tracked_seconds'] for m in members)
    total_prod    = sum(m['productive_seconds'] for m in members)
    total_unprod  = sum(m['unproductive_seconds'] for m in members)
    total_prod_h  = sum((m['productive_hours'] or 0) for m in members)
    prod_base     = total_prod + total_unprod

    totals = {
        'as_of':              as_of.isoformat(),
        'user_count':         len([m for m in members if m['email'] or m['name']]) or len(members),
        'active_users':       len([m for m in members if m['tracked_today']]),
        'total_hours':        round(total_tracked / 3600, 1),
        'productive_hours':   round(total_prod_h, 1),
        'productive_pct':     round(100 * total_prod / prod_base, 1) if prod_base else None,
        'unproductive_pct':   round(100 * total_unprod / prod_base, 1) if prod_base else None,
        'project_count':      len([p for p in projects if not p.get('deleted')]),
        'task_count':         len([t for t in tasks if not t.get('deleted')]),
    }
    return {'totals': totals, 'members': members}


def build_email_html(totals: dict, members: list) -> str:
    """House-style HTML for the daily workforce email. Aggregates only — no
    raw window/app titles (AD-POL-AI-GOV-001). Shared by the pull_timedoctor
    command and the n8n ingest endpoint."""
    navy, orange, ink, mut = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280'
    def pct(v): return '—' if v is None else f'{v}%'
    def hrs(v): return '—' if v is None else f'{v}'
    top = members[:25]
    rows = ''.join(
        f'<tr>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;color:{ink}">{(m.get("name") or m.get("email") or m.get("user_id") or "")[:40]}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;color:{mut}">{(m.get("role") or "")[:14]}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;color:{navy};font-weight:700">{hrs(m.get("productive_hours"))}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;color:{mut}">{pct(m.get("productive_pct"))}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:center">{"✓" if m.get("tracked_today") else "—"}</td>'
        f'</tr>'
        for m in top
    )
    tile = lambda label, val: (
        f'<td style="padding:14px 16px;background:#F8F9FB;border:1px solid #EEF0F3;border-radius:10px">'
        f'<div style="font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:{mut}">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{navy};margin-top:2px">{val}</div></td>'
    )
    return f"""<!doctype html><html><body style="margin:0;background:#EEF0F3;font-family:'Book Antiqua',Georgia,serif">
<div style="max-width:760px;margin:0 auto;background:#fff">
  <div style="background:{navy};padding:18px 24px">
    <div style="color:{orange};font-size:20px;font-weight:700">Alpha Direct — Daily Workforce Report</div>
    <div style="color:#AEB6C2;font-size:13px;margin-top:2px">Time Doctor · {totals.get('as_of','')}</div>
  </div>
  <div style="padding:22px 24px">
    <table cellspacing="8" style="border-collapse:separate;width:100%"><tr>
      {tile('Active users', f"{totals.get('active_users','—')} / {totals.get('user_count','—')}")}
      {tile('Productive hours', hrs(totals.get('productive_hours')))}
      {tile('Productive %', pct(totals.get('productive_pct')))}
    </tr></table>
    <p style="color:{mut};font-size:12px;margin:16px 0 6px">Top {len(top)} by <b>productive hours</b> — time actually spent in apps Time Doctor rates as work, not time with the computer switched on. Per-user totals only; no window titles, no activity detail. Where someone works across more than one machine we report their busiest machine, never the two added together:</p>
    <table style="border-collapse:collapse;width:100%;font-size:13px">
      <thead><tr style="background:{navy}">
        <th style="padding:7px 10px;text-align:left;color:#fff">Employee</th>
        <th style="padding:7px 10px;text-align:left;color:#fff">Role</th>
        <th style="padding:7px 10px;text-align:right;color:#fff">Productive hrs</th>
        <th style="padding:7px 10px;text-align:right;color:#fff">Prod %</th>
        <th style="padding:7px 10px;text-align:center;color:#fff">Tracked</th>
      </tr></thead><tbody>{rows}</tbody>
    </table>
    <p style="color:{mut};font-size:11px;margin-top:16px">{totals.get('project_count','?')} active project(s), {totals.get('task_count','?')} task(s). Full report in omni → HRIS → Time Doctor.</p>
  </div>
</div></body></html>"""
