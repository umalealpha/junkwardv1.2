"""
integrations/td_matching.py

THE one Time Doctor ↔ payroll identity layer (Fable review 2026-07-14).

Why this exists: three different name-matchers grew in the codebase and the
Workforce Brief / Exceptions emails shipped on the two weakest — a prod dry-run
matched only 43 of ~96 trackers and flagged 99 people as "did not track"
(mostly false), and the morning brief's matcher had no unique-hit guard so one
person could be emailed another person's hours. This module is now the ONLY
place Time Doctor users are matched to payroll employees. Do not grow a new
matcher elsewhere — import this one.

Matching order (each pass consumes both sides — a TD account links to at most
one employee and vice versa; ambiguous candidates are NEVER guessed):
  0. Confirmed TimeDoctorUserMap rows (td_user_id is stable → once a human has
     confirmed a link, the whole name-matching bug class is over for them).
  1. Exact email (unique on both sides).
  2. Exact normalised name — "(ExCo)" tags, punctuation, case stripped
     (unique on both sides).
  3. Token-subset ('Wangu Moses' ⊆ 'Wangu W. Moses'; middle names / initials)
     — only on a UNIQUE hit in BOTH directions.

The matcher is TD-account-centric: callers report ONLY employees holding a
matched TD account; "did not track" means confirmed-account + zero hours.
Employees with no TD account at all are the informational ghost list
(`unmatched_employees`) and must never be conflated with did-not-track.
Non-payroll TD accounts (contractors, role/test accounts) fall out into
`unmatched_td` — the human review queue — and are never reported on.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def norm_name(s: str) -> str:
    """Lower-case, drop "(ExCo)"-style tags and punctuation, squeeze spaces."""
    s = (s or '').lower()
    s = re.sub(r'\(.*?\)', '', s)          # drop "(ExCo)" etc.
    s = re.sub(r'[^a-z ]', '', s)
    return ' '.join(s.split())


def name_tokens(norm: str) -> frozenset:
    """Significant name tokens (>=2 chars; drops single initials like 'w')."""
    return frozenset(t for t in norm.split() if len(t) >= 2)


# --- Multi-machine identity merge -------------------------------------------
# Some people run Time Doctor on more than one machine, so their time lands
# under two separate TD accounts and they show up twice (or, if one account's
# name doesn't match payroll, half their hours are dropped). Fold the extra
# accounts onto ONE canonical identity so the person appears once with the
# machines summed. Keyed by TD user_id — stable, survives a display-name change
# — which is the authority; a normalised-name fallback covers accounts whose id
# hasn't been pinned yet.
#
# CFO 2026-07-22: "PrathapAsus" (laptop, was orphaned → hours dropped) +
# "Prathap Ganesharajah (ExCo)" (desktop) → one row "Prathap Ganesharajah +".
# The trailing "+" flags that the row COMBINES machines: Time Doctor tracks per
# device, so if two machines track the same wall-clock moment the hours overlap and
# cannot be de-duplicated from the stored daily totals. norm_name() drops the "+",
# so the canonical name still matches payroll "Prathap Ganesharajah".
#
# The merge itself stays FROZEN (CFO 2026-07-25) — one row per person, do not
# un-merge or remove an alias without double-confirming with him. What CHANGED on
# 2026-07-29 is only how the folded machines are combined: the reported figure is
# now the BUSIEST machine, never the sum. Summing put him at 21.64 h in a 24-hour
# day and that went to all 79 staff as "yesterday's hero"; his real day was 9-10 h.
# See integrations/timedoctor.py::aggregate.
# FROZEN (CFO 2026-07-25): he WANTS his hours reported as ONE merged figure. Do NOT
# un-merge, remove an alias, or change this aggregation without double-confirming with
# him first (two explicit yeses). The desktop is a Mac, the laptop is Windows — the
# merged row's Windows osVersion is cosmetic (folded-in laptop's last device); leave it.
TD_ACCOUNT_ALIASES_BY_UID = {
    'aetqn--rymG6S7qQ': ('XnseOWIRLwAEEwM1', 'Prathap Ganesharajah +'),   # PrathapAsus (laptop, Windows)
    'XnseOWIRLwAEEwM1': ('XnseOWIRLwAEEwM1', 'Prathap Ganesharajah +'),   # Prathap Ganesharajah (ExCo desktop, Mac)
    # Paul Beka runs Time Doctor on two machines (CFO 2026-08-03: "Paul beka is
    # duplicated, merge them as one employee"). The desktop account pbeka@
    # ("Paul Beka (Exco)") is matched to payroll → canonical; the second machine
    # account (SID-gibberish email, "Paul Beka", unmatched) folds onto it. Same
    # busiest-machine rule as Prathap (never summed); norm_name() drops the "+",
    # so the canonical name still matches payroll "Paul Beka".
    'Y8eRQCG4OcfgpaUU': ('Y8eRQCG4OcfgpaUU', 'Paul Beka +'),   # Paul Beka (Exco), pbeka@ — canonical, payroll-matched
    'amHAqdPdPz1edaae': ('Y8eRQCG4OcfgpaUU', 'Paul Beka +'),   # 2nd machine (SID email) — was a separate row
    # Amantle Adelaide Thake (Health Insurance, athake@) SWITCHED accounts, not two
    # concurrent machines (CFO 2026-08-27): the misspelled "Amatle Thake" ran
    # Aug 19-25, then the correctly-spelled "Amantle Thake" from Aug 26. Fold the old
    # onto the current so her WHOLE history counts under one identity — deleting
    # either would erase a real week of her work. Busiest-machine per day; the days
    # barely overlap so there is nothing to double-count.
    'ao7FWooeVHTHZtPk': ('ao7FWooeVHTHZtPk', 'Amantle Thake +'),   # "Amantle Thake" (current, correct) — canonical
    'anrw7sg53nktLafo': ('ao7FWooeVHTHZtPk', 'Amantle Thake +'),   # "Amatle Thake" (old, misspelled) — folds on
}
TD_ACCOUNT_ALIASES_BY_NAME = {
    'prathapasus': ('XnseOWIRLwAEEwM1', 'Prathap Ganesharajah +'),
    'paul beka':   ('Y8eRQCG4OcfgpaUU', 'Paul Beka +'),
    'amantle thake': ('ao7FWooeVHTHZtPk', 'Amantle Thake +'),
    'amatle thake':  ('ao7FWooeVHTHZtPk', 'Amantle Thake +'),
}


# --- Automatic same-person merge (TD-MERGE-02, bug 5dffc022, 2026-09-03) -----
# The table above is hand-typed: Prathap, Paul Beka and Amantle Thake were each
# added only AFTER a human noticed hours going missing. So every new second
# account silently drops that person's hours until they complain.
#
# Natasha Nthite complained. On 2 Sep her Time Doctor client re-registered under
# the Windows machine SID instead of her email, so TD created a second account
# (S-1-5-21-…@<company>.alphadirect.co.bw) carrying the SAME deviceId as her
# confirmed one. Her real 5.73 h at 99.5 % productive landed there; her payroll
# -linked account read 0.0 h, so /my-omni and her morning brief said she did not
# work. The hours were present from the very first 03:00 pull — waiting could
# never have fixed it.
#
# So discover the alias instead of typing it. An UNMATCHED TD account folds onto
# a CONFIRMED payroll-linked one when their normalised display names are equal.
# This is evidence, not a guess: Time Doctor itself is reporting two accounts
# under the identical person name, and a human has already confirmed one of them
# against payroll.
#
# Deliberately conservative — it must never credit one person's hours to
# another:
#   * the fold target must be confirmed=True AND payroll-linked;
#   * the name must carry >= 2 significant tokens, so role rigs ('User',
#     'OTHERS', 'Bokanihp') never attach to anybody;
#   * a name shared by two DIFFERENT confirmed employees is ambiguous → skip;
#   * the hardcoded table always wins.
# An unmatched account with no confirmed twin (Kelvin Kimani, Lorato Molosiwa,
# Unaludo Mafuraga) stays in the review queue for a human — that is a missing
# payroll link, a different problem, and not ours to guess.
#
# The aggregation is UNCHANGED: a merged person still reports the BUSIEST
# machine, never the sum (FROZEN, CFO 2026-07-29).
_DYNAMIC_ALIAS_TTL = 300          # seconds; a newly confirmed link lands within 5 min
_dynamic_alias_cache = {'at': None, 'by_uid': {}}


def reset_dynamic_alias_cache():
    """Drop the memo — for tests, and after confirming a map row by hand."""
    _dynamic_alias_cache['at'] = None
    _dynamic_alias_cache['by_uid'] = {}


def dynamic_account_aliases():
    """{unmatched_td_user_id: (canonical_uid, 'Name +')} read from the user map.

    Memoised for _DYNAMIC_ALIAS_TTL because canonical_identity() is called once
    per person per report. Returns {} — a clean no-op — if the DB is not
    reachable, so no caller can be broken by this lookup.
    """
    import time

    at = _dynamic_alias_cache['at']
    if at is not None and (time.monotonic() - at) < _DYNAMIC_ALIAS_TTL:
        return _dynamic_alias_cache['by_uid']

    aliases = {}
    try:
        from integrations.models import TimeDoctorUserMap

        # Candidate fold targets: one confirmed, payroll-linked account per name.
        # A name held by two different confirmed employees is ambiguous — drop it.
        targets, ambiguous = {}, set()
        for m in TimeDoctorUserMap.objects.filter(confirmed=True,
                                                  employee__isnull=False):
            nm = norm_name(m.td_name or '')
            if len(name_tokens(nm)) < 2:
                continue
            prev = targets.get(nm)
            if prev is not None and prev[2] != m.employee_id:
                ambiguous.add(nm)
                continue
            if prev is None:
                targets[nm] = (str(m.td_user_id), m.td_name or '', m.employee_id)
        for nm in ambiguous:
            targets.pop(nm, None)

        for m in TimeDoctorUserMap.objects.filter(employee__isnull=True):
            uid = str(m.td_user_id)
            if uid in TD_ACCOUNT_ALIASES_BY_UID:
                continue                   # the frozen table already owns this one
            nm = norm_name(m.td_name or '')
            hit = targets.get(nm)
            if not hit or uid == hit[0]:
                continue
            # The target's name may ALREADY carry the merge marker (Paul Beka's
            # canonical row is literally 'Paul Beka +'). Strip it before adding
            # ours, or reports read 'Paul Beka + +'.
            base = (hit[1] or '').rstrip().rstrip('+').rstrip()
            aliases[uid] = (hit[0], f'{base} +')
    except Exception:                      # noqa: BLE001 — never break a report
        log.debug('dynamic TD alias lookup unavailable', exc_info=True)
        return _dynamic_alias_cache['by_uid'] or {}

    _dynamic_alias_cache['at'] = time.monotonic()
    _dynamic_alias_cache['by_uid'] = aliases
    return aliases


def canonical_identity(user_id, name):
    """Map a Time Doctor account to its canonical (user_id, display_name),
    collapsing a person's multiple machines onto one identity. Returns the
    inputs unchanged when no alias applies (the overwhelming common case)."""
    if user_id is not None:
        hit = TD_ACCOUNT_ALIASES_BY_UID.get(str(user_id))
        if hit:
            return hit
    hit = TD_ACCOUNT_ALIASES_BY_NAME.get(norm_name(name or ''))
    if hit:
        return hit
    if user_id is not None:
        hit = dynamic_account_aliases().get(str(user_id))
        if hit:
            return hit
    return (user_id, name)


def fold_uid(user_id):
    """Canonical Time Doctor user_id for a possibly-aliased machine account.
    A no-op for everyone without an alias (returns the input unchanged)."""
    if user_id is None:
        return None
    hit = (TD_ACCOUNT_ALIASES_BY_UID.get(str(user_id))
           or dynamic_account_aliases().get(str(user_id)))
    return hit[0] if hit else user_id


def collapse_users(users):
    """Fold a person's multiple machine accounts into ONE canonical roster entry
    (see TD_ACCOUNT_ALIASES_*), so they are matched and counted once with the
    machines summed. A no-op for everyone without an alias. Keeps the FIRST
    occurrence's other fields (email/role/timezone) under the canonical id/name."""
    out, seen = [], set()
    for u in (users or []):
        uid = u.get('id') or u.get('user_id')
        cid, cname = canonical_identity(uid, u.get('name') or '')
        if str(cid) in seen:
            continue
        seen.add(str(cid))
        out.append({**u, 'id': cid, 'name': cname})
    return out


def active_td_users(users):
    """Drop archived/removed Time Doctor accounts from a roster pull.
    Conservative: only excludes users the API explicitly flags as gone, so a
    schema surprise can never silently empty the roster."""
    out = []
    for u in (users or []):
        status = str(u.get('status') or '').lower()
        if u.get('archived') or u.get('deleted') or status in ('archived', 'removed', 'deleted'):
            continue
        out.append(u)
    return out


class TDMatcher:
    """Match a Time Doctor roster to payroll employees, once, for one report.

    td_users:  list of dicts with 'id' (or 'user_id'), 'name', 'email'.
    employees: payroll.Employee-like objects (.id, .full_name, .email).

    After construction:
      .employee_for_uid    {td_user_id: employee}         (the matched set)
      .uid_for_employee_id {employee.id: td_user_id}
      .unmatched_td        [td user dict, ...]            (review queue)
      .unmatched_employees [employee, ...]                (ghost list)
    """

    def __init__(self, td_users, employees, *, use_map=True):
        self.td = []
        for u in (td_users or []):
            uid = u.get('id') or u.get('user_id')
            if not uid:
                continue
            nm = norm_name(u.get('name') or '')
            self.td.append({'uid': uid, 'raw': u, 'name': u.get('name') or '',
                            'email': (u.get('email') or '').strip().lower(),
                            'nm': nm, 'toks': name_tokens(nm)})
        self.emp = []
        for e in (employees or []):
            nm = norm_name(getattr(e, 'full_name', '') or '')
            self.emp.append({'obj': e, 'email': (getattr(e, 'email', '') or '').strip().lower(),
                             'nm': nm, 'toks': name_tokens(nm)})

        self.employee_for_uid = {}
        self.uid_for_employee_id = {}
        self._used_td, self._used_emp = set(), set()

        if use_map:
            self._pass_confirmed_map()
        self._pass_exact('email')
        self._pass_exact('nm')
        self._pass_token_subset()

        self.unmatched_td = [t['raw'] for i, t in enumerate(self.td) if i not in self._used_td]
        self.unmatched_employees = [e['obj'] for i, e in enumerate(self.emp) if i not in self._used_emp]

    # -- internals ------------------------------------------------------------
    def _link(self, ti: int, ei: int):
        t, e = self.td[ti], self.emp[ei]
        self.employee_for_uid[t['uid']] = e['obj']
        emp_id = getattr(e['obj'], 'id', None)
        if emp_id is not None:
            self.uid_for_employee_id[emp_id] = t['uid']
        self._used_td.add(ti)
        self._used_emp.add(ei)

    def _pass_confirmed_map(self):
        try:
            from integrations.models import TimeDoctorUserMap
            td_by_uid = {str(t['uid']): i for i, t in enumerate(self.td)}
            emp_by_id = {getattr(e['obj'], 'id', None): i for i, e in enumerate(self.emp)}
            rows = TimeDoctorUserMap.objects.filter(confirmed=True, employee__isnull=False)
            for row in rows:
                ti = td_by_uid.get(str(row.td_user_id))
                ei = emp_by_id.get(row.employee_id)
                if ti is None or ei is None or ti in self._used_td or ei in self._used_emp:
                    continue
                self._link(ti, ei)
        except Exception:    # noqa: BLE001
            log.exception('TDMatcher: could not load confirmed TD mappings; '
                          'falling back to name matching only')

    def _pass_exact(self, key: str):
        td_idx, emp_idx = {}, {}
        for i, t in enumerate(self.td):
            if i not in self._used_td and t[key]:
                td_idx.setdefault(t[key], []).append(i)
        for i, e in enumerate(self.emp):
            if i not in self._used_emp and e[key]:
                emp_idx.setdefault(e[key], []).append(i)
        for val, eis in emp_idx.items():
            tis = td_idx.get(val, [])
            # unique-hit guard both ways: duplicated names/emails are never guessed
            if len(eis) == 1 and len(tis) == 1:
                self._link(tis[0], eis[0])

    def _pass_token_subset(self):
        def subset(a, b):
            return len(a) >= 2 and len(b) >= 2 and (a <= b or b <= a)

        for ei, e in enumerate(self.emp):
            if ei in self._used_emp:
                continue
            cands = [ti for ti, t in enumerate(self.td)
                     if ti not in self._used_td and subset(e['toks'], t['toks'])]
            if len(cands) != 1:
                continue    # 0 or 2+ candidates → never guess
            ti = cands[0]
            # reverse guard: the TD account must subset-match ONLY this employee
            back = [ej for ej, e2 in enumerate(self.emp)
                    if ej not in self._used_emp and subset(self.td[ti]['toks'], e2['toks'])]
            if back == [ei]:
                self._link(ti, ei)

    # -- persistence ----------------------------------------------------------
    def persist_suggestions(self):
        """Upsert TimeDoctorUserMap rows so a human can confirm links once and
        work the unmatched queue. NEVER touches a confirmed row. Best-effort —
        a DB hiccup here must not stop a report."""
        try:
            from integrations.models import TimeDoctorUserMap
            emp_by_uid = {str(uid): emp for uid, emp in self.employee_for_uid.items()}
            for t in self.td:
                uid = str(t['uid'])
                emp = emp_by_uid.get(uid)
                row = TimeDoctorUserMap.objects.filter(td_user_id=uid).first()
                if row is None:
                    TimeDoctorUserMap.objects.create(
                        td_user_id=uid, td_name=t['name'][:191], td_email=t['email'][:191],
                        employee=emp, source=TimeDoctorUserMap.Source.AUTO)
                    continue
                if row.confirmed:
                    continue
                changed = []
                if row.td_name != t['name'][:191]:
                    row.td_name = t['name'][:191]; changed.append('td_name')
                if row.td_email != t['email'][:191]:
                    row.td_email = t['email'][:191]; changed.append('td_email')
                new_emp_id = getattr(emp, 'id', None)
                if row.employee_id != new_emp_id:
                    row.employee = emp; changed.append('employee')
                if changed:
                    row.save(update_fields=changed + ['updated_at'])
        except Exception:    # noqa: BLE001
            log.exception('TDMatcher: persisting mapping suggestions failed (report continues)')


# ---------------------------------------------------------------------------
# THE one snapshot -> {employee: hours} resolver (CFO 2026-08-27).
# ---------------------------------------------------------------------------
def hours_by_employee(payload, employees, *, field='hours_tracked'):
    """{employee.id: float hours} for a TimeDoctorDailySnapshot `payload`,
    resolving every person through the CONFIRMED TimeDoctorUserMap (stable
    td_user_id) via TDMatcher — NEVER by display name alone.

    Why this exists: five commands used to read the payload `name` and fuzzy-match
    it to `employee.full_name`. When a person's Time Doctor display name differs
    from their HR name (different surname/spelling on the two systems) that match
    silently returned 0 hours — causing false "you're at 0 / behind" nudges and a
    zero/absent attendance record. The confirmed map already links those accounts
    by stable id; this resolver USES it, so a mapped person can never be read as 0
    because of a name spelling again. Name/email matching survives only as
    TDMatcher's guarded fallback for accounts nobody has confirmed yet.

    Employees with no matched Time Doctor account are ABSENT from the result — the
    caller supplies the default (0.0 or None) and can tell "no TD account" apart
    from "matched, zero hours". `field` picks the payload metric ('hours_tracked'
    or 'productive_hours'). Multi-machine accounts are folded to one canonical id
    (busiest machine, already applied upstream in aggregate())."""
    payload = payload or []
    hours_by_uid = {}
    roster = []
    for m in payload:
        uid = m.get('user_id')
        if not uid:
            continue
        cuid = str(fold_uid(uid))
        # aggregate() already emits one busiest-machine row per person; if an
        # un-folded alias ever slips through, keep the BUSIEST, never the sum.
        hours_by_uid[cuid] = max(hours_by_uid.get(cuid, 0.0), float(m.get(field) or 0))
        roster.append({'id': uid, 'name': m.get('name') or '', 'email': m.get('email') or ''})
    # roster is the STORED snapshot payload (already active at pull time); it carries
    # no archived/status keys, so active_td_users would be a no-op here — and calling
    # it would also swallow the "Time Doctor unreadable" fail-closed test seam that
    # patches active_td_users. Fold multi-machine aliases only.
    matcher = TDMatcher(collapse_users(roster), employees)
    out = {}
    for emp_id, uid in matcher.uid_for_employee_id.items():
        h = hours_by_uid.get(str(fold_uid(uid)))
        if h is not None:
            out[emp_id] = h
    return out
