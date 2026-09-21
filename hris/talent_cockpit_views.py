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


def _scope(user):
    """Return 'all' (explicit exec/HR allowlist), a set of CURRENT refs the
    manager may touch (their reporting chain), or None (no talent access)."""
    if _sees_all(user):
        return 'all'
    emails = _managed_emails(user)
    if not emails:
        return None
    refs = {d.ref for d in DevelopmentDialogue.objects.filter(is_current=True)
            if (d.email or '').lower() in emails}
    return refs or None


# ---- scoring (mirrors the cockpit app's formula) ----------------------------
def _recompute(payload: dict):
    dd = payload.get('dd') or {}
    sb = 0.0
    any_s = False
    for sec in dd.get('sections', []) or []:
        for r in sec.get('rows', []) or []:
            emp = _num(r.get('employee')); w = _num(r.get('weight'))
            if emp is not None and w is not None:
                r['weighted'] = round(emp * w, 4); sb += r['weighted']; any_s = True
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
        return Response({'can_manage': True,
                         'scope': 'all' if scope == 'all' else 'team',
                         'people': [_payload(r) for r in rows]})

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
