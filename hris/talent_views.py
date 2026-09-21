"""
hris/talent_views.py — Talent Management module (TMS Orbit feature parity).

CFO directive 2026-05-26 — close the talent-management gap flagged by Unami
after she compared the live HRIS landing against the AlphaDirect_TMS_Orbit
reference design. The TM cluster is:

  GET  /hris/api/talent/nine-box/        → 9-Box Talent Grid
  GET  /hris/api/talent/succession/      → Succession Planning
  GET  /hris/api/talent/idp/             → Individual Development Plan (me)
  GET  /hris/api/talent/idp/<profile_id>/→ IDP for one specific profile
  GET  /hris/api/talent/ai-readiness/    → Workforce AI readiness rollup

Every endpoint enforces the same three-gate model as the rest of the HRIS
feature pack (whitelist + unlock + capability). View-only — no writes.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db.models import Prefetch
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.models import HRISProfile, PerformanceReview, OKR
from hris.feature_views import _gate, _profile_for
from django.utils import timezone


# ─── 9-Box grid configuration ────────────────────────────────────────────────
# Mirrors AlphaDirect_TMS_Orbit.html BX[] — performance × potential matrix.
# Box number runs 1 (top-right = Star) to 9 (bottom-left = Talent Risk).
#
#   Axis convention:                  Potential ↑
#                                     ┌─────┬─────┬─────┐
#                                     │  3  │  2  │  1  │
#                                     ├─────┼─────┼─────┤
#                                     │  6  │  5  │  4  │
#                                     ├─────┼─────┼─────┤
#                                     │  9  │  8  │  7  │
#                                     └─────┴─────┴─────┘
#                                       Performance →
#
NINE_BOX: dict[str, dict[str, Any]] = {
    'H-H': {'n': 1, 'l': 'Star',                'c': '#0A9396', 't': '#fff',
            'a': 'Retain. Stretch assignment + accelerated promotion path.'},
    'H-M': {'n': 2, 'l': 'High Potential',      'c': '#94D2BD', 't': '#0A2240',
            'a': 'Invest. Targeted development to convert into a Star.'},
    'H-L': {'n': 3, 'l': 'Rough Diamond',       'c': '#E9D8A6', 't': '#0A2240',
            'a': 'Coach. Build confidence + give exposure.'},
    'M-H': {'n': 4, 'l': 'High Performer',      'c': '#005F73', 't': '#fff',
            'a': 'Reward. Retain via merit + recognition.'},
    'M-M': {'n': 5, 'l': 'Core Player',         'c': '#778DA9', 't': '#fff',
            'a': 'Engage. Keep developing — backbone of the team.'},
    'M-L': {'n': 6, 'l': 'Inconsistent',        'c': '#EE9B00', 't': '#0A2240',
            'a': 'Clarify expectations + structured coaching.'},
    'L-H': {'n': 7, 'l': 'Solid Specialist',    'c': '#BB3E03', 't': '#fff',
            'a': 'Deepen. Specialist track — not management.'},
    'L-M': {'n': 8, 'l': 'Underperformer',      'c': '#AE2012', 't': '#fff',
            'a': 'Improvement plan. 90-day PIP with clear targets.'},
    'L-L': {'n': 9, 'l': 'Talent Risk',         'c': '#C1121F', 't': '#fff',
            'a': 'Manage out. Corrective action or exit.'},
}


def _avg(xs):
    nums = [float(x) for x in xs if isinstance(x, (int, float, Decimal))]
    return sum(nums) / len(nums) if nums else 0.0


def _band(score: float) -> str:
    """Map a 1-4 score to L/M/H."""
    if score >= 3.0:
        return 'H'
    if score >= 2.0:
        return 'M'
    return 'L'


def _box_for(perf: float, pot: float) -> dict[str, Any]:
    key = f'{_band(perf)}-{_band(pot)}'
    return {'key': key, **NINE_BOX[key]}


def _box_from_dialogue(dd) -> tuple[dict[str, Any], float, float]:
    """9-box placement from a Development Dialogue's own 0..1 scores.

    The dialogue stores performance/potential on 0..1; the grid bands on a
    1..4 scale, so scale linearly (0→1, 1→4) and reuse _box_for. The thirds
    line up exactly: 1/3 → the M boundary (2.0), 2/3 → the H boundary (3.0).
    Returns (box, perf_1to4, pot_1to4).
    """
    perf = 1.0 + float(dd.performance or 0.0) * 3.0
    pot  = 1.0 + float(dd.potential or 0.0) * 3.0
    return _box_for(perf, pot), round(perf, 2), round(pot, 2)


def _latest_review(profile: HRISProfile) -> PerformanceReview | None:
    return (
        profile.performance_reviews
        .filter(status__in=[PerformanceReview.Status.SUBMITTED,
                            PerformanceReview.Status.FINALISED])
        .order_by('-period')
        .first()
    )


def _talent_payload(profile: HRISProfile) -> dict[str, Any]:
    """Compute the per-employee talent record consumed by every TM view."""
    emp = profile.employee
    pr = _latest_review(profile)

    # Performance = average of all competency scores in the latest review.
    # Falls back to overall_rating if competency_scores is empty.
    perf = 0.0
    if pr:
        comp_vals: list[float] = []
        for vs in (pr.competency_scores or {}).values():
            if isinstance(vs, list):
                comp_vals.extend(float(x) for x in vs
                                 if isinstance(x, (int, float)))
        if comp_vals:
            perf = sum(comp_vals) / len(comp_vals)
        elif pr.overall_rating is not None:
            perf = float(pr.overall_rating)

    pot = _avg(pr.potential_scores if pr else [])
    vals = _avg(pr.values_scores if pr else [])

    # Bug 5c612aea: employees with NO performance review (perf=pot=0) were all
    # banded L-L and dumped into box 9 "Talent Risk" — making it look like the
    # entire workforce is at risk. They are simply UNRATED; keep them out of the
    # boxes until someone is actually scored.
    rated = bool(pr and (perf > 0 or pot > 0))
    if rated:
        box = _box_for(perf, pot)
    else:
        box = {'key': 'UNRATED', 'n': 0, 'l': 'Unrated', 'c': '#9CA3AF', 't': '#FFFFFF',
               'a': 'No performance review on file yet — not placed on the grid.'}

    # OKR rollup — weighted avg of (score_h1 + score_h2)/2 by weight_pct.
    okr_score = 0.0
    okr_weight = 0.0
    okrs = OKR.objects.filter(profile=profile).order_by('-period')[:8]
    for o in okrs:
        s1 = float(o.score_h1 or 0)
        s2 = float(o.score_h2 or 0)
        w  = float(o.weight_pct or 0) / 100.0
        if w > 0:
            okr_score  += ((s1 + s2) / 2.0) * w
            okr_weight += w

    okr_avg = okr_score / okr_weight if okr_weight > 0 else 0.0

    return {
        'profile_id':     str(profile.id),
        'employee_id':    str(emp.id),
        'name':           emp.full_name,
        'position':       emp.job_title or '',
        'department':     emp.department or '',
        'company':        emp.company.code if emp.company else '',
        'grade':          profile.grade.code if profile.grade else '',
        'initials':       profile.initials or _initials(emp.full_name),
        'talent_segment': profile.talent_segment or '',
        'perf_score':     round(perf, 2),
        'pot_score':      round(pot,  2),
        'values_score':   round(vals, 2),
        'okr_score':      round(okr_avg, 2),
        'box':            box,
        'rated':          rated,
        'review_period':  pr.period if pr else None,
        'review_date':    pr.review_date.isoformat() if (pr and pr.review_date) else None,
    }


def _initials(full_name: str) -> str:
    parts = (full_name or '').strip().split()
    if not parts:
        return '??'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


def _overlay_current_dialogues(request, rows, emp_email):
    """Let the current Development Dialogue drive a talent row's placement.

    A current dialogue — signed off or not — is the source of truth for that
    employee's performance / potential, ahead of the legacy PerformanceReview
    (Unami / CFO 2026-07-23, opened up to unsigned dialogues by the CFO on
    2026-07-31). Signed beats unsigned for the same person, and each placed row
    carries `signed` so the grid can mark a draft as provisional. Employees with
    no current dialogue keep their legacy placement, so views never empty out
    while the org migrates onto dialogues. Company scope applies here too.

    Mutates each matched row in place (box / perf_score / pot_score / rated /
    review_period / source='dialogue') and returns how many rows were placed
    from a dialogue. Shared by the 9-box grid (item 6) and succession (item 7c).
    """
    from hris.talent_cockpit_models import DevelopmentDialogue
    from core.mixins import apply_company_scope
    # CFO 2026-07-31: place people from their CURRENT dialogue whether or not it
    # has been signed off. Requiring `locked` meant 0 of 7 dialogues reached the
    # grid — the link worked perfectly and showed nothing, which reads as broken.
    # An unsigned dialogue is still the newest assessment of that person; it is
    # marked provisional (`signed: False`) so the grid can show it as draft rather
    # than pretend it is final.
    dd_qs = apply_company_scope(
        request,
        DevelopmentDialogue.objects.filter(is_current=True),
        'employee__company_id',
    )
    dd_by_emp: dict[str, Any] = {}
    dd_by_email: dict[str, Any] = {}
    for dd in dd_qs.only('employee_id', 'email', 'performance', 'potential',
                         'period', 'locked'):
        # A signed dialogue always beats an unsigned one for the same person.
        if dd.employee_id:
            cur = dd_by_emp.get(str(dd.employee_id))
            if cur is None or (dd.locked and not cur.locked):
                dd_by_emp[str(dd.employee_id)] = dd
        if dd.email:
            key = dd.email.strip().lower()
            cur = dd_by_email.get(key)
            if cur is None or (dd.locked and not cur.locked):
                dd_by_email[key] = dd

    placed = 0
    for r in rows:
        dd = dd_by_emp.get(r['employee_id']) or dd_by_email.get(emp_email.get(r['employee_id'], ''))
        if dd is None:
            continue
        box, perf, pot = _box_from_dialogue(dd)
        r['box'] = box
        r['perf_score'] = perf
        r['pot_score'] = pot
        r['rated'] = True
        r['review_period'] = dd.period or r.get('review_period')
        r['source'] = 'dialogue'
        r['signed'] = bool(dd.locked)      # False = provisional, not yet signed off
        placed += 1
    return placed


# ─── 9-Box Talent Grid ───────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def nine_box(request):
    """GET /hris/api/talent/nine-box/

    Returns every active HRIS profile with their computed 9-box placement
    plus an aggregated cell distribution suitable for the grid view.
    """
    denied = _gate(request, capability='view_talent')
    if denied is not None:
        return denied

    profiles = (
        HRISProfile.objects
        .select_related('employee', 'employee__company', 'grade')
        .prefetch_related(
            Prefetch(
                'performance_reviews',
                queryset=PerformanceReview.objects.order_by('-period'),
            ),
        )
        .filter(employee__status='active')
    )
    # Entity scope (CFO 2026-06-16) — clamp to caller's granted companies.
    from core.mixins import apply_company_scope
    profiles = apply_company_scope(request, profiles, 'employee__company_id')

    # Defensive — a single bad profile must NEVER 5xx the whole grid.
    # CFO + Unami both reported HTTP 502 on 2026-06-05; root cause was an
    # uncaught exception inside _talent_payload (a profile with a missing
    # grade reference). Now we skip rotten rows + log them.
    import logging as _log
    _logger = _log.getLogger(__name__)
    rows = []
    emp_email: dict[str, str] = {}
    skipped = 0
    for p in profiles:
        try:
            payload = _talent_payload(p)
        except Exception as e:  # noqa: BLE001
            skipped += 1
            _logger.warning('nine_box: skipping profile %s: %s', p.pk, e)
            continue
        rows.append(payload)
        if p.employee and p.employee.email:
            emp_email[payload['employee_id']] = p.employee.email.strip().lower()
    if skipped:
        _logger.info('nine_box: %d/%d profiles skipped', skipped, len(rows)+skipped)

    # Item 6 (Unami / CFO directive 2026-07-23): a manager-signed (locked,
    # current) Development Dialogue drives grid placement; employees without one
    # keep their legacy PerformanceReview placement. Shared with succession (7c).
    dialogue_placed = _overlay_current_dialogues(request, rows, emp_email)

    # Bucket counts per cell — RATED employees only. Unrated people are not
    # placed on the grid (bug 5c612aea) and pct is over the rated population.
    rated_rows = [r for r in rows if r.get('rated')]
    cells: dict[str, list[str]] = defaultdict(list)
    for r in rated_rows:
        cells[r['box']['key']].append(r['profile_id'])

    grid = []
    for key, cfg in NINE_BOX.items():
        ids = cells.get(key, [])
        grid.append({
            'key':   key,
            'box_n': cfg['n'],
            'label': cfg['l'],
            'color': cfg['c'],
            'text':  cfg['t'],
            'action': cfg['a'],
            'count': len(ids),
            'pct':   round(len(ids) / max(len(rated_rows), 1) * 100.0, 1),
            'profile_ids': ids,
        })

    return Response({
        'as_of':       timezone.localdate().isoformat(),
        'total':       len(rows),
        'rated':       len(rated_rows),
        'unrated':     len(rows) - len(rated_rows),
        'skipped':     skipped,
        'dialogue_placed': dialogue_placed,
        'grid':        grid,
        'employees':   rows,
    })


# ─── Succession Planning ─────────────────────────────────────────────────────

# Critical job-title patterns. Anything matching one of these (case-insensitive)
# counts as a critical role for the succession view. Kept in code so HR can
# review changes through PR; can move to a model later if it churns often.
CRITICAL_ROLE_PATTERNS = [
    'chief executive', 'ceo',
    'chief financial', 'cfo',
    'chief operating', 'coo',
    'chief technology', 'cto',
    'chief risk',
    'head of',
    'general manager',
    'director',
    'principal officer',
    'manager',
]


def _is_critical(job_title: str) -> bool:
    t = (job_title or '').lower()
    return any(p in t for p in CRITICAL_ROLE_PATTERNS)


# Readiness map: which talent segments are ready to step up, and how soon.
READINESS_BY_SEGMENT: dict[str, dict[str, Any]] = {
    'star':              {'label': 'Ready now',         'months': 0,  'score': 90},
    'high_potential':    {'label': 'Ready 6-12 months', 'months': 9,  'score': 75},
    'solid_performer':   {'label': 'Ready 12-24 months','months': 18, 'score': 55},
    'specialist':        {'label': 'Not for this role', 'months': 36, 'score': 25},
    'developing':        {'label': 'Long-term',         'months': 24, 'score': 35},
    'underperforming':   {'label': 'Not ready',         'months': 36, 'score': 10},
    'new_hire':          {'label': 'Too new',           'months': 24, 'score': 20},
    '':                  {'label': 'Not assessed',      'months': 24, 'score': 30},
}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def succession(request):
    """GET /hris/api/talent/succession/

    Returns each critical-role incumbent and up to three internal
    successor candidates ranked by readiness × performance score.
    """
    denied = _gate(request, capability='view_talent')
    if denied is not None:
        return denied

    from core.mixins import apply_company_scope
    _succ_qs = (
        HRISProfile.objects
        .select_related('employee', 'employee__company', 'manager', 'grade')
        .prefetch_related(
            Prefetch(
                'performance_reviews',
                queryset=PerformanceReview.objects.order_by('-period'),
            ),
        )
        .filter(employee__status='active')
    )
    # Entity scope (CFO 2026-06-16) — clamp to caller's granted companies.
    profiles = list(apply_company_scope(request, _succ_qs, 'employee__company_id'))

    pool = []
    emp_email: dict[str, str] = {}
    for p in profiles:
        payload = _talent_payload(p)
        pool.append(payload)
        if p.employee and p.employee.email:
            emp_email[payload['employee_id']] = p.employee.email.strip().lower()
    # Item 7c (Unami / CFO directive 2026-07-23): succession reads completed
    # (manager-signed, current) Development Dialogues where present, so successor
    # readiness reflects the live dialogue — not only the legacy PerformanceReview.
    _overlay_current_dialogues(request, pool, emp_email)
    by_id = {p['profile_id']: p for p in pool}
    by_dept: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pool:
        by_dept[p['department']].append(p)

    # Reporting tree, for the successor pools (CFO 2026-08-30). `manager` is a FK
    # to payroll.Employee, so subordinates are keyed by the manager's Employee id.
    subs_by_mgr: dict[str, list[str]] = defaultdict(list)   # Employee id -> [profile_id]
    prof_emp: dict[str, str] = {}                           # profile_id -> Employee id
    for pr2 in profiles:
        prof_emp[str(pr2.id)] = str(pr2.employee_id)
        if pr2.manager_id:
            subs_by_mgr[str(pr2.manager_id)].append(str(pr2.id))

    # CFO/HR-named successors (override) — these take the top slots in rank order;
    # the auto downline ranking fills any remaining slots (CFO 2026-08-30).
    from hris.models import SuccessionNominee
    nominees_by_inc: dict[str, list[str]] = defaultdict(list)   # incumbent pid -> [nominee pid] in rank order
    for nm in SuccessionNominee.objects.order_by('incumbent_id', 'rank').values_list('incumbent_id', 'nominee_id'):
        nominees_by_inc[str(nm[0])].append(str(nm[1]))

    def _score_card(cand: dict[str, Any], named: bool = False) -> dict[str, Any]:
        seg = cand['talent_segment'] or ''
        ready = READINESS_BY_SEGMENT.get(seg, READINESS_BY_SEGMENT[''])
        score = (ready['score'] * 0.60
                 + (cand['perf_score'] / 4.0 * 100) * 0.25
                 + (cand['pot_score'] / 4.0 * 100) * 0.15)
        return {**cand, 'readiness': ready['label'],
                'readiness_score': round(score, 1),
                'months_to_ready': ready['months'], 'named': named}

    roles = []
    for prof in profiles:
        emp = prof.employee
        if not _is_critical(emp.job_title):
            continue
        incumbent = by_id.get(str(prof.id))
        if incumbent is None:
            continue

        # Successor pool = the incumbent's DOWNLINE (their whole reporting tree),
        # ranked by readiness. Because every candidate reports UP to the
        # incumbent, a superior or a peer can never appear — the CEO is not in
        # the CFO's downline, which is exactly what kills the "CEO succeeds the
        # CFO" nonsense (CFO 2026-08-30). And the true feeders surface naturally:
        # the Finance Manager + Financial Controller for the CFO, the COO/CFO for
        # the CEO. Replaces the old same-department pool that pulled peer execs.
        inc_emp = str(prof.employee_id)
        seen_pids: set[str] = set()
        stack = [inc_emp]
        team_pids: list[str] = []
        while stack:
            mid = stack.pop()
            for pid in subs_by_mgr.get(mid, []):
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                team_pids.append(pid)
                stack.append(prof_emp.get(pid, ''))
        # Combined score: readiness 60% + performance 25% + potential 15%.
        candidates = [_score_card(by_id[pid]) for pid in team_pids if by_id.get(pid)]
        candidates.sort(key=lambda c: -c['readiness_score'])

        # CFO/HR-named picks (override) lead in rank order; the auto ranking
        # fills the rest. A named nominee who has left simply drops out (by_id
        # holds active profiles only).
        named_pids = nominees_by_inc.get(str(prof.id), [])
        if named_pids:
            named_cards, named_set = [], set()
            for npid in named_pids:
                cand = by_id.get(npid)
                if cand is None or npid in named_set or cand['profile_id'] == incumbent['profile_id']:
                    continue
                named_cards.append(_score_card(cand, named=True))
                named_set.add(npid)
            candidates = named_cards + [c for c in candidates if c['profile_id'] not in named_set]

        # Depth flag — colour-coded for the UI.
        depth = len(candidates)
        ready_now = sum(1 for c in candidates if c['months_to_ready'] == 0)
        if depth == 0:
            risk = 'critical'
        elif ready_now == 0 and depth < 2:
            risk = 'high'
        elif ready_now == 0:
            risk = 'medium'
        else:
            risk = 'low'

        roles.append({
            'role':       emp.job_title,
            'incumbent':  incumbent,
            'department': emp.department or '',
            'company':    emp.company.code if emp.company else '',
            'risk':       risk,
            'depth':      depth,
            'ready_now':  ready_now,
            'successors': candidates[:3],
        })

    roles.sort(key=lambda r: ({'critical': 0, 'high': 1, 'medium': 2, 'low': 3}[r['risk']],
                              r['role']))

    return Response({
        'as_of':          timezone.localdate().isoformat(),
        'critical_roles': len(roles),
        'dialogue_sourced': sum(1 for p in pool if p.get('source') == 'dialogue'),
        'can_edit':       True,   # reaching here means the caller passed view_talent
        'roles':          roles,
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def succession_nominees(request, incumbent_id):
    """GET/POST /api/v1/talent/succession/<incumbent_id>/nominees/

    Lets HR/CFO NAME the successors for a role (override the auto ranking).
    GET  → the current named successors + a pick-list of active employees.
    POST {nominee_ids:[...]} → replace the named successors; order = rank.
    Same view_talent gate as the succession page (CFO 2026-08-30).
    """
    denied = _gate(request, capability='view_talent')
    if denied is not None:
        return denied
    from hris.models import SuccessionNominee
    inc = HRISProfile.objects.filter(id=incumbent_id).select_related('employee').first()
    if inc is None:
        return Response({'detail': 'Unknown incumbent.'}, status=404)

    def _p(prof):
        e = getattr(prof, 'employee', None)
        return {'profile_id': str(prof.id),
                'name': e.full_name if e else '',
                'position': (e.job_title or '') if e else '',
                'department': (e.department or '') if e else ''}

    if request.method == 'POST':
        ids = request.data.get('nominee_ids') if isinstance(request.data, dict) else None
        if not isinstance(ids, list):
            return Response({'detail': 'nominee_ids must be a list of profile ids.'}, status=400)
        ids = [str(i) for i in ids][:3]
        valid = {str(p.id) for p in HRISProfile.objects.filter(id__in=ids)}
        SuccessionNominee.objects.filter(incumbent=inc).delete()
        rank = 1
        for i in ids:
            if i in valid and i != str(inc.id):
                SuccessionNominee.objects.create(incumbent=inc, nominee_id=i, rank=rank)
                rank += 1

    current = (SuccessionNominee.objects.filter(incumbent=inc)
               .order_by('rank').select_related('nominee', 'nominee__employee'))
    named = [{'rank': n.rank, **_p(n.nominee)} for n in current if n.nominee]
    options = [_p(p) for p in (HRISProfile.objects
                               .filter(employee__status='active')
                               .exclude(id=inc.id)
                               .select_related('employee')
                               .order_by('employee__full_name'))]
    return Response({'incumbent': _p(inc), 'named': named, 'options': options})


# ─── Individual Development Plan ─────────────────────────────────────────────

# Suggested learning interventions per competency gap (score < 3 / 4).
IDP_RECOMMENDATIONS: dict[str, list[str]] = {
    'LD': ['Leadership essentials course (LinkedIn Learning)',
           'Cross-functional shadow assignment with a senior leader',
           'Quarterly 360° feedback'],
    'BU': ['Insurance economics module (CII or AIIB)',
           'Read latest NBFIRA market brief + write a 1-page summary',
           'Sit in on one product-pricing call per quarter'],
    'RE': ['Treaty reinsurance fundamentals (Hannover Re online)',
           'Pair-program a treaty placement with the CFO',
           'Attend AIIB regional reinsurance day'],
    'PE': ['ECDL or Microsoft 365 specialist track',
           'AI-prompting workshop (Alpha Stack n8n + Open WebUI)',
           'Document one process every month for the runbook library'],
    'DI': ['Inclusion fundamentals course',
           'Mentor a junior team member outside your department',
           'Volunteer for the diversity working group'],
}


def _idp_payload(profile: HRISProfile) -> dict[str, Any]:
    base = _talent_payload(profile)
    pr = _latest_review(profile)

    # Gaps: competency areas where the avg of the 4 sub-scores < 3.
    gaps = []
    if pr:
        for code, scores in (pr.competency_scores or {}).items():
            if not isinstance(scores, list) or not scores:
                continue
            try:
                m = sum(float(x) for x in scores) / len(scores)
            except (TypeError, ValueError):
                continue
            if m < 3.0:
                gaps.append({
                    'code':            code,
                    'score':           round(m, 2),
                    'recommendations': IDP_RECOMMENDATIONS.get(code, [
                        'Targeted coaching with line manager',
                        'Identify a development project and report monthly',
                    ]),
                })
        gaps.sort(key=lambda g: g['score'])

    # Stretch goal — derive from box label.
    stretch = base['box']['a']

    # 12-month roadmap (canonical 4 quarters).
    today = timezone.localdate()
    roadmap = []
    for i in range(1, 5):
        roadmap.append({
            'quarter':   f"Q{i}",
            'starts':    (today + _dt.timedelta(days=90 * (i - 1))).isoformat(),
            'theme':     _quarter_theme(i, gaps, base['box']['l']),
        })

    return {
        **base,
        'gaps':    gaps,
        'stretch': stretch,
        'roadmap': roadmap,
    }


def _quarter_theme(q: int, gaps: list[dict[str, Any]], box_label: str) -> str:
    if not gaps:
        return ['Stretch + visibility', 'Project leadership',
                'Cross-functional rotation', 'Coaching others'][q - 1]
    top = gaps[0]['code'] if gaps else None
    by_code = {
        'LD': 'Leadership development',
        'BU': 'Business acumen',
        'RE': 'Reinsurance technical depth',
        'PE': 'Productivity + digital skills',
        'DI': 'Diversity + inclusion',
    }
    primary = by_code.get(top, 'Core skill building')
    return [
        f'Diagnose: {primary} baseline',
        f'Practise: {primary} on a live project',
        f'Demonstrate: {primary} with measurable outcome',
        f'Embed + share: teach the team',
    ][q - 1]


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_idp(request):
    """GET /hris/api/talent/idp/

    Returns the IDP for the calling user. ESS-accessible.
    """
    denied = _gate(request)
    if denied is not None:
        return denied
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'detail': 'No HRIS profile linked to this account.'},
                        status=404)
    return Response(_idp_payload(profile))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def idp_for_profile(request, profile_id):
    """GET /hris/api/talent/idp/<uuid:profile_id>/

    Returns the IDP for an arbitrary profile. Manager / HR only.
    """
    denied = _gate(request, capability='view_talent')
    if denied is not None:
        return denied
    from core.mixins import scoped_company_ids
    qs = (
        HRISProfile.objects
        .select_related('employee', 'employee__company', 'grade')
        .filter(pk=profile_id)
    )
    # Entity scope (CFO 2026-06-16): a UUID outside the caller's grant 404s,
    # so a scoped user can't pull another entity's profile by guessing ids.
    ids = scoped_company_ids(request)
    if ids is not None:
        qs = qs.filter(employee__company_id__in=ids) if ids else qs.none()
    profile = qs.first()
    if profile is None:
        return Response({'detail': 'Profile not found.'}, status=404)
    return Response(_idp_payload(profile))


# ─── AI Readiness ────────────────────────────────────────────────────────────

# Map talent segment → AI-readiness tier. Captures the assumption that high-
# performers + high-potential staff convert AI training into productivity
# faster, while underperformers + new hires need more scaffolding.
AI_READINESS_TIER: dict[str, str] = {
    'star':            'Ready',
    'high_potential':  'Ready',
    'solid_performer': 'Developing',
    'specialist':      'Developing',
    'developing':      'Aware',
    'new_hire':        'Aware',
    'underperforming': 'Aware',
    '':                'Not assessed',
}

# Tier scoring — used to compute the org-wide readiness percentage.
AI_TIER_SCORE = {'Ready': 100, 'Developing': 65, 'Aware': 30, 'Not assessed': 0}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ai_readiness(request):
    """GET /hris/api/talent/ai-readiness/

    Returns a workforce-wide AI literacy readout. Each employee gets a tier
    based on their talent segment + a personal-effectiveness (PE) competency
    score; the response also rolls up per-department and overall.
    """
    denied = _gate(request, capability='view_talent')
    if denied is not None:
        return denied

    from core.mixins import apply_company_scope
    _air_qs = (
        HRISProfile.objects
        .select_related('employee', 'employee__company', 'grade')
        .prefetch_related(
            Prefetch(
                'performance_reviews',
                queryset=PerformanceReview.objects.order_by('-period'),
            ),
        )
        .filter(employee__status='active')
    )
    # Entity scope (CFO 2026-06-16) — clamp to caller's granted companies.
    profiles = list(apply_company_scope(request, _air_qs, 'employee__company_id'))

    rows = []
    by_dept: dict[str, list[str]] = defaultdict(list)
    for prof in profiles:
        pl = _talent_payload(prof)
        seg = pl['talent_segment']
        tier = AI_READINESS_TIER.get(seg, AI_READINESS_TIER[''])

        # Personal effectiveness score — pull the PE competency average when
        # the latest review has one.
        pe = 0.0
        pr = _latest_review(prof)
        if pr and isinstance(pr.competency_scores, dict):
            pe_list = pr.competency_scores.get('PE')
            if isinstance(pe_list, list) and pe_list:
                pe = sum(float(x) for x in pe_list) / len(pe_list)

        # Promote one tier if PE >= 3.0; demote one tier if PE < 2.0.
        if pe >= 3.0 and tier == 'Developing':
            tier = 'Ready'
        elif pe >= 3.0 and tier == 'Aware':
            tier = 'Developing'
        elif pe < 2.0 and tier == 'Ready':
            tier = 'Developing'
        elif pe < 2.0 and tier == 'Developing':
            tier = 'Aware'

        rows.append({**pl, 'pe_score': round(pe, 2), 'ai_tier': tier})
        by_dept[pl['department']].append(tier)

    counts = {'Ready': 0, 'Developing': 0, 'Aware': 0, 'Not assessed': 0}
    for r in rows:
        counts[r['ai_tier']] += 1

    total = max(len(rows), 1)
    org_score = sum(AI_TIER_SCORE[t] * c for t, c in counts.items()) / total

    depts = []
    for d, tiers in sorted(by_dept.items()):
        c = {'Ready': 0, 'Developing': 0, 'Aware': 0, 'Not assessed': 0}
        for t in tiers:
            c[t] += 1
        depts.append({
            'department':  d or '— Unassigned —',
            'headcount':   len(tiers),
            'breakdown':   c,
            'score':       round(
                sum(AI_TIER_SCORE[t] * cnt for t, cnt in c.items()) / max(len(tiers), 1),
                1,
            ),
        })

    depts.sort(key=lambda x: -x['score'])

    return Response({
        'as_of':       timezone.localdate().isoformat(),
        'total':       len(rows),
        'org_score':   round(org_score, 1),
        'counts':      counts,
        'departments': depts,
        'employees':   rows,
    })
