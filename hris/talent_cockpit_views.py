"""hris/talent_cockpit_views.py — Development Dialogue "Talent Cockpit" API.

omni is the performance-evaluation system of record (CFO directive 2026-07-17).

Access model:
  * Exec (CEO/COO/CFO) + HR (HR tier) — see & edit EVERYONE (full cockpit).
  * A manager — sees & edits ONLY their downward reporting chain (scoped
    server-side; a manager can never touch a non-report's record).
  * Any employee — sees & self-assesses ONLY their own (/my-dialogue).

Features: period versioning (copy→new period, history kept), employee
self-assessment, sign-off + lock, bulk "new period for all". Every write is
audited (AuditableMixin) with the acting user.
"""
from __future__ import annotations

import copy
import os

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.dd_template import needs_template, seed_into
from hris.feature_views import _gate
from hris.models import DevelopmentDialogue, HRISProfile
from payroll.models import Employee

# Who may see EVERY employee's dialogue. Deliberately TIGHT and explicit —
# CFO directive 2026-07-18: "HR, Arun, Arjun and I are privy to everyone's".
# NOT derived from the broad HRIS 'hr' role, so a stray HR_MANAGER role on a
# vendor/payroll account can never expose confidential appraisals. Managers
# still see their own reporting chain via _scope; everyone else sees only their
# own (my-dialogue). Env-overridable.
DD_ALL_LOCAL_PARTS = frozenset({
    'pganesharajah',  # CFO
    'aiyer',          # CEO — Arun Iyer
    'arjuniyer',      # COO — Arjun Iyer
    'unami', 'ubutale',  # Unami Butale (HR) — either local-part
    'dikgopoleng',    # Dorothy Ikgopoleng (HR)
})


def _dd_all_local_parts() -> set:
    env = (os.environ.get('OMNI_DD_ALL_LOCAL_PARTS') or '').strip()
    if env:
        return {x.strip().lower() for x in env.split(',') if x.strip()}
    return set(DD_ALL_LOCAL_PARTS)


def _sees_all(user) -> bool:
    """True only for true superusers + the explicit exec/HR allowlist —
    NOT for anyone merely holding an HR role."""
    if getattr(user, 'is_superuser', False):
        return True
    local = (getattr(user, 'email', '') or '').split('@')[0].strip().lower()
    return bool(local) and local in _dd_all_local_parts()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp01(v):
    n = _num(v)
    return None if n is None else max(0.0, min(1.0, n))


# ---- reporting-chain scoping ------------------------------------------------
def _caller_employee(user):
    email = (getattr(user, 'email', '') or '').strip().lower()
    return Employee.objects.filter(email__iexact=email).first() if email else None


def _downward_employee_ids(me) -> set:
    ids: set = set()
    frontier = [me.id]
    while frontier:
        reps = [r for r in HRISProfile.objects.filter(manager_id__in=frontier)
                .exclude(employee_id__in=ids).values_list('employee_id', flat=True)
                if r and r != me.id]
        if not reps:
            break
        ids.update(reps)
        frontier = reps
    return ids


def _managed_emails(user) -> set:
    me = _caller_employee(user)
    if not me:
        return set()
    ids = _downward_employee_ids(me)
    return {(e or '').lower() for e in Employee.objects.filter(id__in=ids)
            .values_list('email', flat=True) if e}


class _TeamScope:
    """A manager's reach: the CURRENT dialogue refs they may touch, plus the
    work emails of their whole reporting chain.

    The emails matter on their own. Scope used to be the set of refs alone, so a
    manager whose reports had no dialogue yet resolved to an EMPTY set and the
    cockpit answered 403 — the first dialogue could never be started, which is
    exactly the state every new manager is in.
    """

    def __init__(self, refs, emails):
        self.refs = frozenset(refs)
        self.emails = frozenset(emails)

    def __contains__(self, ref):
        return ref in self.refs

    def __iter__(self):
        return iter(self.refs)

    def covers_email(self, email) -> bool:
        return (email or '').strip().lower() in self.emails


def _scope(user):
    """Return 'all' (explicit exec/HR allowlist), a _TeamScope for a manager
    (their reporting chain), or None (no talent access)."""
    if _sees_all(user):
        return 'all'
    emails = _managed_emails(user)
    if not emails:
        return None
    refs = {d.ref for d in DevelopmentDialogue.objects.filter(is_current=True)
            if (d.email or '').lower() in emails}
    return _TeamScope(refs, emails)


# ---- scoring (mirrors the cockpit app's formula) ----------------------------
def _recompute(payload: dict):
    dd = payload.get('dd') or {}
    sb = 0.0
    any_s = False
    for sec in dd.get('sections', []) or []:
        for r in sec.get('rows', []) or []:
            # D2 (CFO 21-Sep-2026): the headline follows the MANAGER's score, not
            # the employee self-score. Previously this used r['employee'], so a
            # reviewer could move a score 2→5 and the overall % did not shift, in
            # front of the person being reviewed. Forward-only — callers never
            # recompute a locked/signed period, so past ratings are untouched.
            mgr = _num(r.get('manager')); w = _num(r.get('weight'))
            if mgr is not None and w is not None:
                r['weighted'] = round(mgr * w, 4); sb += r['weighted']; any_s = True
            else:
                r['weighted'] = None
    dd['sectionB'] = round(sb, 4) if any_s else None
    raw = 0.0
    any_v = False
    for v in dd.get('values', []) or []:
        rw = _num(v.get('raw')); w = _num(v.get('weight'))
        if rw is not None and w is not None:
            v['weighted'] = round(rw * w, 4); raw += v['weighted']; any_v = True
        else:
            v['weighted'] = None
    dd['valuesScore'] = round(raw * 0.2, 4) if any_v else None
    oa = (dd.get('sectionB') or 0) + (dd.get('valuesScore') or 0)
    dd['overallAll'] = round(oa, 4) if (any_s or any_v) else None
    payload['dd'] = dd
    if dd.get('overallAll') is not None:
        payload['overall'] = round(dd['overallAll'] * 100, 1)
    return payload.get('overall')


def _match_employee(email: str, name: str):
    """Best-effort payroll link for a dialogue. Email first (exact, unambiguous);
    full name only when it matches exactly ONE active employee — the roster holds
    duplicate names, and attaching an appraisal to the wrong person is worse than
    leaving it unlinked."""
    email = (email or '').strip().lower()
    if email:
        hit = Employee.objects.filter(email__iexact=email).first()
        if hit:
            return hit
    name = (name or '').strip()
    if name:
        hits = list(Employee.objects.filter(full_name__iexact=name, status='active')[:2])
        if len(hits) == 1:
            return hits[0]
    return None


def _person_email(p: dict, ref: str) -> str:
    """The dialogue owner's work email, from the person object or the ref.

    The cockpit app mints its person id as `<email>::<period>`, so the ref is a
    reliable second source when the person object itself carries no email field.
    """
    for key in ('email', 'workEmail', 'work_email'):
        v = str(p.get(key) or '').strip()
        if '@' in v:
            return v[:200]
    details = (p.get('dd') or {}).get('details') if isinstance(p.get('dd'), dict) else None
    if isinstance(details, dict):
        for key in ('email', 'workEmail'):
            v = str(details.get(key) or '').strip()
            if '@' in v:
                return v[:200]
    head = str(ref or '').split('::', 1)[0].strip()
    return head[:200] if '@' in head else ''


def _row_from_person(row: DevelopmentDialogue, p: dict) -> None:
    row.name       = str(p.get('name', '') or '')[:200]
    row.department = str(p.get('dept', '') or '')[:200]
    row.position   = str(p.get('position', '') or '')[:200]
    row.period     = str(p.get('period', '') or '')[:120]
    row.supervisor = str(p.get('supervisor', '') or '')[:200]
    row.color      = str(p.get('color', '') or '')[:9]
    perf = _num(p.get('performance'));  row.performance = 0.5 if perf is None else perf
    pot  = _num(p.get('potential'));    row.potential   = 0.5 if pot is None else pot
    row.overall = _num(p.get('overall'))
    row.rating  = str(p.get('rating', '') or '')[:120]
    # Owner email + payroll link. Neither was ever set on save, so a dialogue
    # created in the cockpit came out orphaned: `email` blank means the person
    # cannot open their OWN dialogue (my-dialogue looks up by email) and the 9-box
    # overlay cannot match them (it matches on employee_id or email). Every one of
    # the 7 live dialogues has employee_id = None today, including the CFO's.
    # Setting these on save is what makes a new dialogue reach the grid at all.
    email = _person_email(p, getattr(row, 'ref', '') or '')
    if email:
        row.email = email
    if row.employee_id is None:
        row.employee = _match_employee(row.email, row.name)
    # Strip derived/column keys so the stored payload doesn't accrete metadata
    # across GET→PUT round-trips (they are re-added by _payload on read).
    # `signoff` IS real payload data and is kept.
    row.payload = {k: v for k, v in p.items()
                   if k not in ('_ref', 'periodLabel', 'personKey', 'locked',
                                'company', 'grade', 'manager', 'deptCanonical')}
    # A dialogue with no competency structure is not scorable — the manager
    # opens it and there is nothing to fill in. Fill in the framework, MERGING:
    # `dd` also carries the development plan, career aspirations, priorities,
    # measures, the manager's comments and the rating, and this runs on the
    # field-scoped section save too. Assigning a fresh dd here would destroy all
    # of that and answer 200 — the exact wipe `save_section` was written to stop.
    if needs_template(row.payload.get('dd')):
        row.payload['dd'] = seed_into(row.payload.get('dd'))


def _filter_keys(row: DevelopmentDialogue) -> dict:
    """Entity, grade, manager and canonical department for the cockpit filters.

    Read-only and derived on every GET — they are stripped again on save
    (_row_from_person) so they can never round-trip into the stored payload.
    A dialogue with no payroll link (an external person) answers '' and the
    filters group those under "Unlinked".
    """
    employee = row.employee
    if employee is None:
        return {'company': '', 'grade': '', 'manager': '', 'deptCanonical': ''}
    profile = getattr(employee, 'hris_profile', None)
    grade = getattr(profile, 'grade', None) if profile else None
    manager = getattr(profile, 'manager', None) if profile else None
    return {
        'company': employee.company.code if employee.company_id else '',
        'grade': (grade.code if grade else ''),
        'manager': (manager.full_name if manager else ''),
        'deptCanonical': employee.department or '',
    }


def _payload(row: DevelopmentDialogue) -> dict:
    p = dict(row.payload or {})
    p['_ref'] = row.ref
    p['periodLabel'] = row.period
    p['personKey'] = row.person_key or row.email or row.ref
    p['locked'] = row.locked
    p['signoff'] = (row.payload or {}).get('signoff') or {}
    p.update(_filter_keys(row))
    return p


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def cockpit(request):
    scope = _scope(request.user)
    if scope is None:
        return Response({'detail': 'You do not have access to the Talent Cockpit.'},
                        status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        # select_related: _payload reads employee → company / hris_profile →
        # grade + manager for the filter keys, which is 4 queries per person
        # without this.
        rows = list(DevelopmentDialogue.objects.filter(is_current=True)
                    .select_related('employee', 'employee__company',
                                    'employee__hris_profile',
                                    'employee__hris_profile__grade',
                                    'employee__hris_profile__manager'))
        if scope != 'all':
            rows = [r for r in rows if r.ref in scope]
        local = (getattr(request.user, 'email', '') or '').split('@')[0].strip().lower()
        # The live-review "Last time" rail needs each person's PREVIOUS period.
        # One extra query for the whole page (never per row).
        keys = [r.person_key for r in rows if r.person_key]
        prev_map = {}
        for pr in (DevelopmentDialogue.objects.filter(person_key__in=keys, is_current=False)
                   .order_by('person_key', '-created_at')):
            prev_map.setdefault(pr.person_key, pr)
        people = []
        for r in rows:
            d = _payload(r)
            prev = prev_map.get(r.person_key)
            if prev is not None:
                pdd = (prev.payload or {}).get('dd') or {}
                d['prevDD'] = {'sections': pdd.get('sections') or [],
                               'overall': prev.overall, 'rating': prev.rating,
                               'box': _box_name(prev.performance, prev.potential)}
            people.append(d)
        return Response({'can_manage': True,
                         'scope': 'all' if scope == 'all' else 'team',
                         # D1: the live-review greeting needs to know who is looking.
                         'me': {'name': (request.user.get_full_name() or '').strip(),
                                'email': getattr(request.user, 'email', '') or '',
                                'isCFO': local in ('pganesharajah', 'cfo')},
                         'nine_box': NINE_BOX,   # T9: one canonical list for the finale grid
                         'people': people})

    # ---- PUT: save the CURRENT-period dataset (scoped, lock-aware) ----
    people = request.data.get('people')
    if not isinstance(people, list):
        return Response({'detail': 'Body must be {"people": [...]}.'},
                        status=status.HTTP_400_BAD_REQUEST)
    allowed = None if scope == 'all' else scope
    seen_refs = set()
    with transaction.atomic():
        for p in people:
            if not isinstance(p, dict):
                continue
            ref = str(p.get('id'))
            if not ref or ref == 'None':
                continue
            # scoped users may only touch refs they manage; new people (unknown
            # ref) are exec/HR-only.
            existing = DevelopmentDialogue.objects.filter(ref=ref).first()
            if allowed is not None and ref not in allowed:
                # A manager may START a first dialogue for someone in their own
                # reporting chain. Anything else out of scope is skipped — and
                # an existing row is never adopted this way, only a NEW one.
                #
                # The ref is checked against the email it claims, because the
                # email arrives in the CLIENT's payload: without this, a manager
                # could pass a report's address alongside ANY ref and mint a row
                # under a key that is not theirs. The cockpit mints its ids as
                # `<email>::<period>`, so the ref must lead with the address the
                # create was authorised on.
                new_email = _person_email(p, ref)
                ref_head = ref.split('::', 1)[0].strip().lower()
                # ...and only a FIRST one. `ref` is unique but `person_key` is
                # not, so without this a manager could mint `neo@x::2099`,
                # `neo@x::2100` … and the same person would appear twice in the
                # cockpit list and twice in the nine-box. A further period is
                # opened with new_period, which archives the current one.
                already = (bool(new_email) and DevelopmentDialogue.objects
                           .filter(is_current=True, email__iexact=new_email).exists())
                if not (existing is None
                        and not already
                        and allowed.covers_email(new_email)
                        and ref_head == new_email.strip().lower()):
                    continue
            seen_refs.add(ref)
            if existing and existing.locked:
                continue  # signed-off period is read-only
            row = existing or DevelopmentDialogue(ref=ref)
            _row_from_person(row, p)
            row.is_current = True
            if not row.person_key:
                row.person_key = row.email or ref
            row.save(audit_user=request.user)
        # NB: a bulk save NEVER deletes people. Omitting someone from the list
        # (a partial/filtered save) must not wipe them — that footgun deleted 6
        # real records once. Person removal is an explicit action → delete_person.

    return Response({'saved': len(seen_refs)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def delete_person(request):
    """Explicitly remove ONE current dialogue (the cockpit 'remove person'
    action). Scoped, never deletes a locked/signed-off period, audited. This is
    the ONLY way to delete via the cockpit — the bulk save never deletes."""
    scope = _scope(request.user)
    if scope is None:
        return Response({'detail': 'No access.'}, status=status.HTTP_403_FORBIDDEN)
    ref = str(request.data.get('ref') or '')
    if not ref:
        return Response({'detail': 'ref is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if scope != 'all' and ref not in scope:
        return Response({'detail': 'Out of scope.'}, status=status.HTTP_403_FORBIDDEN)
    row = DevelopmentDialogue.objects.filter(ref=ref, is_current=True).first()
    if row is None:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if row.locked:
        return Response({'detail': 'This review is signed off and locked.'},
                        status=status.HTTP_403_FORBIDDEN)
    row.delete(audit_user=request.user)
    return Response({'deleted': ref})


def _clone_new_period(cur: DevelopmentDialogue, period: str, user) -> DevelopmentDialogue:
    key = cur.person_key or cur.email or cur.ref
    base = f"{key}::{period}".replace(' ', '_')[:76]
    new_ref = base
    i = 2
    while DevelopmentDialogue.objects.filter(ref=new_ref).exists():
        new_ref = f"{base}_{i}"[:80]
        i += 1
    cur.is_current = False
    cur.save(audit_user=user)
    payload = copy.deepcopy(cur.payload or {})
    payload['id'] = new_ref
    payload['period'] = period
    payload.pop('signoff', None)  # a fresh period starts unsigned
    new = DevelopmentDialogue(ref=new_ref, person_key=key, is_current=True,
                              locked=False, email=cur.email, employee=cur.employee)
    _row_from_person(new, payload)
    new.period = period
    new.save(audit_user=user)
    return new


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def new_period(request):
    """Copy a person's CURRENT dialogue into a NEW period for re-assessment.
    Archives the current (kept on record) and creates a fresh current one."""
    scope = _scope(request.user)
    if scope is None:
        return Response({'detail': 'No access.'}, status=status.HTTP_403_FORBIDDEN)
    ref = str(request.data.get('ref') or '')
    period = str(request.data.get('period') or '').strip()[:120]
    if not ref or not period:
        return Response({'detail': 'ref and period are required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if scope != 'all' and ref not in scope:
        return Response({'detail': 'Out of scope.'}, status=status.HTTP_403_FORBIDDEN)
    cur = DevelopmentDialogue.objects.filter(ref=ref, is_current=True).first()
    if cur is None:
        return Response({'detail': 'Current dialogue not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    with transaction.atomic():
        new = _clone_new_period(cur, period, request.user)
    return Response({'person': _payload(new)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def new_period_all(request):
    """Open a new period for EVERYONE in scope in one go (year-end roll)."""
    scope = _scope(request.user)
    if scope is None:
        return Response({'detail': 'No access.'}, status=status.HTTP_403_FORBIDDEN)
    period = str(request.data.get('period') or '').strip()[:120]
    if not period:
        return Response({'detail': 'period is required.'}, status=status.HTTP_400_BAD_REQUEST)
    rows = list(DevelopmentDialogue.objects.filter(is_current=True))
    if scope != 'all':
        rows = [r for r in rows if r.ref in scope]
    n = 0
    with transaction.atomic():
        for cur in rows:
            _clone_new_period(cur, period, request.user)
            n += 1
    return Response({'rolled': n})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sign(request):
    """Sign off a dialogue. role in {employee, manager, moderator}.
    Employee signs their own; manager/moderator sign within scope/HR. A manager
    sign-off LOCKS the period (no further edits)."""
    ref = str(request.data.get('ref') or '')
    role = str(request.data.get('role') or '').strip().lower()
    if role not in ('employee', 'manager', 'moderator') or not ref:
        return Response({'detail': 'ref and a valid role are required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    row = DevelopmentDialogue.objects.filter(ref=ref, is_current=True).first()
    if row is None:
        return Response({'detail': 'Dialogue not found.'}, status=status.HTTP_404_NOT_FOUND)

    email = (getattr(request.user, 'email', '') or '').strip().lower()
    if role == 'employee':
        if (row.email or '').lower() != email:
            return Response({'detail': 'You can only sign your own review.'},
                            status=status.HTTP_403_FORBIDDEN)
    else:
        scope = _scope(request.user)
        if scope is None or (scope != 'all' and ref not in scope):
            return Response({'detail': 'Out of scope.'}, status=status.HTTP_403_FORBIDDEN)

    who = (getattr(request.user, 'get_full_name', lambda: '')() or email or 'user')
    payload = dict(row.payload or {})
    signoff = dict(payload.get('signoff') or {})
    signoff[role] = {'by': who, 'at': timezone.now().isoformat(timespec='minutes')}
    payload['signoff'] = signoff
    row.payload = payload
    if role in ('manager', 'moderator'):
        row.locked = True
    row.save(audit_user=request.user)

    # Route the sign-off to the right place (best-effort; never blocks the sign).
    from core import notifications
    if role == 'employee':
        notifications.notify_dialogue_submitted(row, request.user)
    else:
        notifications.close_dialogue_review_tasks(row, signer=request.user)

    return Response({'signoff': signoff, 'locked': row.locked})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def history(request):
    """All periods on record for one person (read-only), newest first.
    Allowed for exec/HR, and for a manager whose scope includes the person."""
    key = str(request.query_params.get('key') or '')
    if not key:
        return Response({'periods': []})
    scope = _scope(request.user)
    if scope is None:
        # allow an employee to see their own history
        cur = DevelopmentDialogue.objects.filter(person_key=key, is_current=True).first()
        if not cur or (cur.email or '').lower() != (getattr(request.user, 'email', '') or '').lower():
            return Response({'detail': 'No access.'}, status=status.HTTP_403_FORBIDDEN)
    elif scope != 'all':
        cur = DevelopmentDialogue.objects.filter(person_key=key, is_current=True).first()
        if not cur or cur.ref not in scope:
            return Response({'detail': 'Out of scope.'}, status=status.HTTP_403_FORBIDDEN)
    rows = (DevelopmentDialogue.objects.filter(person_key=key)
            .order_by('-is_current', '-created_at'))
    return Response({'periods': [
        {'ref': r.ref, 'period': r.period, 'is_current': r.is_current,
         'overall': r.overall, 'rating': r.rating, 'locked': r.locked,
         'payload': _payload(r)} for r in rows]})


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def my_dialogue(request):
    """Self-service: the caller's OWN current dialogue. GET to view; PUT to
    self-assess (employee scores + comments only) while the period is open."""
    denied = _gate(request, capability='view_own_assessment')
    if denied is not None:
        return denied
    email = (getattr(request.user, 'email', '') or '').strip().lower()
    row = None
    if email:
        base = DevelopmentDialogue.objects.filter(is_current=True)
        row = base.filter(email__iexact=email).first()
        if row is None and '@' in email:
            row = base.filter(email__istartswith=email.split('@')[0] + '@').first()

    if request.method == 'GET':
        # A plain-English 9-box + score summary for the My Omni "My Growth" box
        # (CFO 2026-08-31: "show the score of the last feedback received + summary
        # of the 9-box grid"). Reuses the dialogue's stored performance/potential
        # (0..1) — no new data. Bands are plain words, clearer to a non-HR reader
        # than the jargon box names.
        growth = None
        if row is not None:
            perf = _num(getattr(row, 'performance', None))
            pot = _num(getattr(row, 'potential', None))
            if perf is not None or pot is not None:
                def _band(x):
                    x = x or 0.0
                    return 'High' if x > 0.66 else ('Solid' if x > 0.33 else 'Developing')
                growth = {
                    'performance_pct': round((perf or 0.0) * 100),
                    'potential_pct': round((pot or 0.0) * 100),
                    'perf_band': _band(perf),
                    'pot_band': _band(pot),
                    'nine_box': f'{_band(perf)} performance · {_band(pot)} potential',
                }
        return Response({'person': (_payload(row) if row else None), 'growth': growth})

    # ---- PUT: employee self-assessment (own record, own fields only) ----
    if row is None:
        return Response({'detail': 'No dialogue on file for your account.'},
                        status=status.HTTP_404_NOT_FOUND)
    if row.locked:
        return Response({'detail': 'This review is signed off and locked.'},
                        status=status.HTTP_403_FORBIDDEN)
    incoming = request.data.get('person') or {}
    inc_dd = (incoming.get('dd') or {}) if isinstance(incoming, dict) else {}
    payload = dict(row.payload or {})
    dd = payload.get('dd') or {}
    # merge ONLY employee-editable fields: competency employee score + selfComment,
    # value self score. Manager scores, weights, rating, scope are untouched.
    for si, sec in enumerate(dd.get('sections', []) or []):
        inc_sec = (inc_dd.get('sections', []) or [])[si] if si < len(inc_dd.get('sections', []) or []) else {}
        for ri, r in enumerate(sec.get('rows', []) or []):
            inc_rows = inc_sec.get('rows', []) or []
            inc_r = inc_rows[ri] if ri < len(inc_rows) else {}
            if 'employee' in inc_r:
                r['employee'] = _clamp01(inc_r.get('employee'))
            if 'selfComment' in inc_r:
                r['selfComment'] = str(inc_r.get('selfComment') or '')[:2000]
    for vi, v in enumerate(dd.get('values', []) or []):
        inc_vals = inc_dd.get('values', []) or []
        inc_v = inc_vals[vi] if vi < len(inc_vals) else {}
        if 'self' in inc_v:
            v['self'] = _clamp01(inc_v.get('self'))
    payload['dd'] = dd
    if isinstance(incoming, dict) and 'selfSummary' in incoming:
        payload['selfSummary'] = str(incoming.get('selfSummary') or '')[:4000]
    _recompute(payload)
    _row_from_person(row, payload)
    row.is_current = True
    row.save(audit_user=request.user)
    return Response({'person': _payload(row)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def team_dialogues(request):
    """A manager's TEAM dialogues (read-only summary) — downward chain only."""
    denied = _gate(request, capability='view_own_assessment')
    if denied is not None:
        return denied
    me = _caller_employee(request.user)
    if me is None:
        return Response({'people': [], 'manager': None})
    ids = _downward_employee_ids(me)
    if not ids:
        return Response({'people': [], 'manager': me.full_name})
    team_emails = {(e or '').lower() for e in Employee.objects.filter(id__in=ids)
                   .values_list('email', flat=True) if e}
    people = [_payload(d) for d in DevelopmentDialogue.objects.filter(is_current=True)
              if (d.email or '').lower() in team_emails]
    return Response({'people': people, 'manager': me.full_name})


# ── Employee picker for "create a dialogue for a REAL person" ────────────────
# Board item [DD] (CFO QC, 14-Sep-2026): the cockpit's "+ Add person" created a
# synthetic record called "New team member" with no email, so the dialogue came
# out ORPHANED — the person could not open their own (my-dialogue looks up by
# email) and the 9-box grid could not place them. The only way to create a real
# employee's dialogue was to type their details and hope the name matched.
# This endpoint is the roster the picker chooses from.

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def talent_employees(request):
    """Active employees the caller may open a dialogue for.

    Scoped with the SAME rule as the cockpit itself: exec/HR see everyone, a
    manager sees only their own reporting chain, anyone else gets 403. Returning
    the full roster to a manager would leak the org chart to someone the cockpit
    otherwise shows nothing to.
    """
    # _scope() is derived from EXISTING dialogues, so a manager whose reports
    # have none yet comes back None — and that is precisely the manager who
    # needs this list to create the first one. Fall back to the reporting chain
    # itself, which is what the scope is really about.
    scope = _scope(request.user)
    managed = _managed_emails(request.user)
    if scope is None and not managed:
        return Response({'detail': 'You do not have access to the Talent Cockpit.'},
                        status=status.HTTP_403_FORBIDDEN)

    qs = (Employee.objects.filter(status='active')
          .exclude(email='')
          .order_by('full_name'))

    if scope != 'all':
        # A manager may only start dialogues for their own chain.
        qs = [e for e in qs if (e.email or '').lower() in managed]
    else:
        qs = list(qs)

    # Who already HAS a current dialogue — so the picker can say so instead of
    # letting someone create a second one for the same person.
    taken = {(d.email or '').lower()
             for d in DevelopmentDialogue.objects.filter(is_current=True)
             if (d.email or '').strip()}

    out = []
    for e in qs:
        email = (e.email or '').strip()
        out.append({
            'employee_id': e.pk,
            'name':        e.full_name or '',
            'email':       email,
            'department':  getattr(e, 'department', '') or '',
            'position':    getattr(e, 'job_title', '') or getattr(e, 'position', '') or '',
            'employee_no': str(getattr(e, 'employee_number', '') or ''),
            'has_dialogue': email.lower() in taken,
        })
    return Response({'employees': out,
                     'scope': 'all' if scope == 'all' else 'team'})


# ===========================================================================
# Development Dialogue LIVE-REVIEW rebuild (board dd515fa8, CFO 21-Sep-2026)
# ===========================================================================

# Employee-owned fields a MANAGER save must never overwrite (mirror of the
# my_dialogue self-assessment: the employee owns their score + self comment).
_EMPLOYEE_OWNED = ('employee', 'selfComment', 'self')


def _merge_row(stored: dict, inc: dict) -> None:
    """Field-scoped merge of one incoming row onto the stored row. Manager
    fields overwrite; employee-owned fields are left exactly as stored. This is
    the pattern that keeps a section-by-section save from wiping anything."""
    for k, v in (inc or {}).items():
        if k in _EMPLOYEE_OWNED:
            continue
        if k == 'manager':
            stored[k] = _clamp01(v)
        elif k == 'weight':
            stored[k] = _num(v)
        else:
            stored[k] = v


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_section(request):
    """Manager-side save of ONE part of a live review, field-merged into the
    stored payload so the other parts are never touched (T1). A save that would
    leave a section with fewer rows than are stored is REFUSED unless it carries
    an explicit `remove` flag — that silent drop wiped four of five sections
    once. Scoped and lock-aware."""
    scope = _scope(request.user)
    if scope is None:
        return Response({'detail': 'You do not have access to the Talent Cockpit.'},
                        status=status.HTTP_403_FORBIDDEN)
    ref = str(request.data.get('ref') or '')
    row = DevelopmentDialogue.objects.filter(ref=ref, is_current=True).first()
    if row is None:
        return Response({'detail': 'Dialogue not found.'}, status=status.HTTP_404_NOT_FOUND)
    if scope != 'all' and ref not in scope:
        return Response({'detail': 'Out of scope.'}, status=status.HTTP_403_FORBIDDEN)
    if row.locked:
        return Response({'detail': 'This review is signed off and locked.'},
                        status=status.HTTP_403_FORBIDDEN)

    part = str(request.data.get('part') or 'section')
    remove_ok = bool(request.data.get('remove'))
    payload = dict(row.payload or {})
    dd = payload.get('dd') or {}

    if part == 'section':
        try:
            idx = int(request.data.get('index'))
        except (TypeError, ValueError):
            return Response({'detail': 'A section index is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        secs = dd.get('sections') or []
        if idx < 0 or idx >= len(secs):
            return Response({'detail': 'Unknown section.'},
                            status=status.HTTP_400_BAD_REQUEST)
        secs[idx].setdefault('rows', [])          # a section may arrive with no rows key
        stored_rows = secs[idx]['rows']
        inc_rows = request.data.get('rows') or []
        if len(inc_rows) < len(stored_rows) and not remove_ok:
            return Response(
                {'detail': 'Refusing to save: fewer rows than are on record for '
                           'this section. Remove a row with the explicit remove '
                           'action, not by leaving it out of a save.'},
                status=status.HTTP_400_BAD_REQUEST)
        for ri in range(min(len(stored_rows), len(inc_rows))):
            _merge_row(stored_rows[ri], inc_rows[ri])
        if len(inc_rows) > len(stored_rows):                 # explicit appends
            stored_rows.extend(inc_rows[len(stored_rows):])
        elif remove_ok and len(inc_rows) < len(stored_rows):  # explicit removal
            del stored_rows[len(inc_rows):]

    elif part == 'values':
        stored_vals = dd.get('values') or []
        inc_vals = request.data.get('values') or []
        if len(inc_vals) < len(stored_vals) and not remove_ok:
            return Response({'detail': 'Refusing to save: fewer values than on record.'},
                            status=status.HTTP_400_BAD_REQUEST)
        for vi, sv in enumerate(stored_vals):
            if vi < len(inc_vals):
                for k, v in (inc_vals[vi] or {}).items():
                    if k in ('self',):
                        continue
                    sv[k] = _clamp01(v) if k == 'raw' else v

    elif part in ('development', 'verdict'):
        # Top-level narrative + placement fields. Merge only what arrives; never
        # blank a field the save did not mention.
        fields = request.data.get('fields') or {}
        for k in ('pdp', 'careerAspirations', 'developmentPriorities',
                  'developmentMeasures', 'targets', 'managerComment', 'rating'):
            if k in fields:
                dd[k] = fields[k]
        for k in ('performance', 'potential'):
            if k in fields:
                payload[k] = _clamp01(fields[k])
        if 'rating' in fields:
            payload['rating'] = str(fields['rating'] or '')[:120]
    else:
        return Response({'detail': f'Unknown part "{part}".'},
                        status=status.HTTP_400_BAD_REQUEST)

    payload['dd'] = dd
    _recompute(payload)
    payload['stepIndex'] = request.data.get('stepIndex', payload.get('stepIndex'))
    _row_from_person(row, payload)
    row.is_current = True
    row.save(audit_user=request.user)
    return Response({'person': _payload(row), 'saved_at': timezone.localtime().strftime('%H:%M')})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def moderator_challenge(request):
    """The moderator (a later-period reviewer, D4) records a CHALLENGE alongside a
    signed-off dialogue. CFO 21-Sep-2026: a sealed review is never unlocked or
    rewritten — the challenge is a separate, attributed, audited layer, and the
    manager's original rating is left exactly as signed."""
    scope = _scope(request.user)
    if scope is None:
        return Response({'detail': 'You are not a moderator for the Talent Cockpit.'},
                        status=status.HTTP_403_FORBIDDEN)
    ref = str(request.data.get('ref') or '')
    row = DevelopmentDialogue.objects.filter(ref=ref, is_current=True).first()
    if row is None:
        return Response({'detail': 'Dialogue not found.'}, status=status.HTTP_404_NOT_FOUND)
    if scope != 'all' and ref not in scope:
        return Response({'detail': 'Out of scope.'}, status=status.HTTP_403_FORBIDDEN)
    if not row.locked:
        return Response({'detail': 'A challenge only applies to a signed-off review.'},
                        status=status.HTTP_400_BAD_REQUEST)

    who = (getattr(request.user, 'get_full_name', lambda: '')()
           or getattr(request.user, 'email', '') or 'moderator')
    payload = dict(row.payload or {})
    # Stored as its own layer. The original dd is NOT read or written here.
    ch = {
        'by': who,
        'at': timezone.now().isoformat(timespec='minutes'),
        'reason': str(request.data.get('reason') or '')[:4000],
        'sections': request.data.get('sections') or [],
        'overall': _num(request.data.get('overall')),
    }
    # Append — a second moderator must never erase the first. `challenge` keeps
    # the latest for readers that want just one; `challenges` is the full record.
    payload.setdefault('challenges', [])
    payload['challenges'].append(ch)
    payload['challenge'] = ch
    row.payload = payload
    row.save(audit_user=request.user)   # row.locked stays True; dd untouched
    return Response({'challenge': ch, 'challenges': payload['challenges'], 'locked': row.locked})


# One canonical nine-box list, so the finale grid and the succession grid can
# never name the same square differently (T9). perf/pot are the low/med/high
# band each square sits in; `line` is the plain sentence shown to the employee.
NINE_BOX = [
    {'name': 'Star',             'perf': 'high', 'pot': 'high',
     'line': 'Top talent — high performance and high potential.'},
    {'name': 'High Potential',   'perf': 'med',  'pot': 'high',
     'line': 'Strong potential, performance still growing into the role.'},
    {'name': 'Rough Diamond',    'perf': 'low',  'pot': 'high',
     'line': 'High potential not yet showing in results — invest and stretch.'},
    {'name': 'High Performer',   'perf': 'high', 'pot': 'med',
     'line': 'Delivers strongly and reliably in the current role.'},
    {'name': 'Core Player',      'perf': 'med',  'pot': 'med',
     'line': 'Solid, dependable contributor — the backbone of the team.'},
    {'name': 'Inconsistent',     'perf': 'low',  'pot': 'med',
     'line': 'Results vary — needs support to perform consistently.'},
    {'name': 'Solid Specialist', 'perf': 'high', 'pot': 'low',
     'line': 'Deep expert in the role; growth is in depth, not breadth.'},
    {'name': 'Underperformer',   'perf': 'med',  'pot': 'low',
     'line': 'Below expectation — a clear improvement plan is needed.'},
    {'name': 'Talent Risk',      'perf': 'low',  'pot': 'low',
     'line': 'Low performance and potential — act with care and urgency.'},
]


_BOX_BY_BAND = {(b['perf'], b['pot']): b['name'] for b in NINE_BOX}


def _box_name(performance, potential) -> str:
    """Canonical nine-box name for a perf/potential pair (0..1). Same bands as the
    client so the rail, grid and finale never disagree (T9)."""
    perf = _num(performance) or 0.0
    pot = _num(potential) or 0.0
    pb = 'high' if perf >= 0.6667 else ('med' if perf >= 0.3333 else 'low')
    qb = 'high' if pot > 0.6667 else ('med' if pot > 0.3333 else 'low')
    return _BOX_BY_BAND.get((pb, qb), '')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def nine_box_labels(request):
    """The single source of truth for the nine-box square names + sentences."""
    return Response({'labels': NINE_BOX})
