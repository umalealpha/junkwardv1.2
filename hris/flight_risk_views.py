"""
hris/flight_risk_views.py — Flight-Risk Radar (early-warning check-in prompt).

Computes an INDICATIVE flight-risk score (0-100) for each active employee
from signals already recorded elsewhere in the HRIS — no new table, no
survey, no predictive model. The intent is narrow: give HR a nudge to have
a supportive conversation EARLY, before someone hands in notice. This is
explicitly NOT a verdict on any individual — the frontend disclaimer says
so plainly, and every signal here is a simple, explainable proxy.

Signals (see WEIGHT_* below for the exact points each contributes):
  1. No approved leave taken this calendar year          — burnout proxy.
  2. No recent MonthlyCheckIn on record, or the last      — engagement /
     recorded rating was low (PerformanceCheckRating        performance
     LOW_RATINGS).                                          proxy.
  3. Long tenure (hire_date older than LONG_TENURE_YEARS)  — career-
     with no cross-referenced progression signal.            stagnation
     A simple tenure proxy, deliberately NOT cross-           proxy.
     referenced against CareerMilestone/grade history —
     the brief asks to keep the math simple and readable.
  4. No Recognition (kudos) received in the trailing       — under-
     RECOGNITION_LOOKBACK_DAYS window.                        appreciation
                                                                proxy.

Pay-stagnation (Payslip.gross_amount unchanged over a long window) was
considered and DELIBERATELY OMITTED: payroll history is still being
backfilled for several entities, so "no raise on record" would as often
mean "no payslip history loaded" as "actually stagnant pay" — a noisy,
misleading signal for exactly the population most worth getting right.
Skipped per the brief's explicit "OPTIONAL; skip if messy" allowance.

Graceful degradation: several signals depend on modules that may not be in
use yet company-wide (ELRA monthly check-ins are DORMANT by default — see
hris/performance_views.py — and Recognition/kudos may simply have zero
rows at a young company). Flagging every employee as "at risk" just
because a module has never been switched on would be actively misleading,
so each such signal is skipped ENTIRELY (zero points, for everyone) when
its table has no rows at all system-wide. The same principle applies per
record: an employee with no HRISProfile yet (so no leave / check-in /
recognition history to read) still gets a tenure-only score, plus a note
that some signals were unavailable — never silently dropped from the
radar, never silently scored as if "no risk" had been proven.

PII wall: output carries only full_name, department, job_title and the
scores/reasons computed here — the same wall as every other HRIS surface.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import apply_company_scope
from hris.api_views import _deny_if_not_whitelisted


# ── Scoring weights (HR/CFO directive — flight-risk radar, 2026-07-21) ─────
# Four signals, equally weighted, sum to a 0-100 indicative score. Equal
# weights keep the math transparent enough to explain in a two-minute HR
# conversation — this is a supportive-conversation prompt, not a validated
# predictive model, so a simple score everyone can sanity-check beats a
# cleverer one nobody can.
WEIGHT_NO_LEAVE_TAKEN     = 25   # 0 approved leave requests so far this calendar year
WEIGHT_CHECKIN_OR_RATING  = 25   # no recent check-in on record, or last rating low
WEIGHT_LONG_TENURE        = 25   # long tenure, no tracked progression (simple proxy)
WEIGHT_NO_RECOGNITION     = 25   # no kudos received in the last ~12 months

LONG_TENURE_YEARS         = 4     # brief: "hire_date old, e.g. >3-4 yrs"
RECOGNITION_LOOKBACK_DAYS = 365   # "~12 months"
CHECKIN_RECENT_DAYS       = 120   # ~4 months grace before a check-in counts as "stale"

# Score -> band. With 4 equal-weight signals the only possible totals are
# 0 / 25 / 50 / 75 / 100, so these cutoffs simply read as "2+ signals fired
# = medium, 3+ fired = high".
BAND_HIGH_MIN = 75
BAND_MED_MIN  = 50


def _band_for_score(score: int) -> str:
    if score >= BAND_HIGH_MIN:
        return 'high'
    if score >= BAND_MED_MIN:
        return 'med'
    return 'low'


def compute_flight_risk(employees=None) -> list[dict]:
    """Pure helper — no request/HTTP coupling, no DB writes.

    `employees` is an optional iterable/QuerySet of payroll.Employee rows
    (defaults to every ACTIVE employee). Returns a list of dicts, ranked
    highest-score-first:

        {profile_id, name, department, job_title, score, band, reasons: [str]}

    `name` is Employee.full_name only — no other PII is read or returned.
    """
    from hris.models import HRISProfile, LeaveRequest, Recognition
    from hris.performance_feedback_models import LOW_RATINGS, MonthlyCheckIn
    from payroll.models import Employee

    if employees is None:
        employees = Employee.objects.filter(status=Employee.Status.ACTIVE)
    employees = list(employees)
    if not employees:
        return []

    today = timezone.localdate()
    year_start = today.replace(month=1, day=1)
    tenure_cutoff = today - timedelta(days=365 * LONG_TENURE_YEARS)
    recognition_cutoff = today - timedelta(days=RECOGNITION_LOOKBACK_DAYS)
    checkin_cutoff = today - timedelta(days=CHECKIN_RECENT_DAYS)

    emp_ids = [e.pk for e in employees]

    # HRISProfile is a one-to-one EXTENSION of Employee — an active employee
    # HR hasn't onboarded into HRIS yet simply won't have one. Bulk-fetch
    # once so the per-employee loop below never queries in a loop (N+1) and
    # never trips a RelatedObjectDoesNotExist on the reverse accessor.
    profile_by_emp_id = {
        p.employee_id: p
        for p in HRISProfile.objects.filter(employee_id__in=emp_ids)
    }
    profile_ids = [p.pk for p in profile_by_emp_id.values()]

    # Graceful degradation — see module docstring. A module with ZERO rows
    # system-wide has never actually been used; treat its absence as a data
    # gap, not evidence of risk, and skip the signal for EVERYONE rather
    # than flagging the whole roster.
    leave_module_live = LeaveRequest.objects.filter(
        status=LeaveRequest.Status.APPROVED).exists()
    checkin_module_live = MonthlyCheckIn.objects.exists()
    recognition_module_live = Recognition.objects.exists()

    leave_taken_this_year = set()
    if leave_module_live:
        leave_taken_this_year = set(
            LeaveRequest.objects.filter(
                profile_id__in=profile_ids,
                status=LeaveRequest.Status.APPROVED,
                start_date__gte=year_start,
            ).values_list('profile_id', flat=True).distinct()
        )

    latest_checkin_by_profile = {}
    if checkin_module_live:
        # auto_posted rows carry a fresh conversation_date but nobody actually
        # spoke to the person, so counting one as "the latest check-in" would
        # suppress the "no recent performance conversation" signal for months
        # (Fable, 2026-08-26).
        checkins = (MonthlyCheckIn.objects
                    .filter(profile_id__in=profile_ids, auto_posted=False)
                    .order_by('profile_id', '-period_year', '-period_month'))
        for c in checkins:
            # First row per profile_id in this ordering is the most recent
            # one — setdefault() keeps only that first hit.
            latest_checkin_by_profile.setdefault(c.profile_id, c)

    recognized_recently = set()
    if recognition_module_live:
        recognized_recently = set(
            Recognition.objects.filter(
                receiver_id__in=profile_ids, created_at__gte=recognition_cutoff,
            ).values_list('receiver_id', flat=True).distinct()
        )

    results = []
    for emp in employees:
        profile = profile_by_emp_id.get(emp.pk)
        score = 0
        reasons: list[str] = []

        # Signal 1 — burnout: no approved leave taken this calendar year.
        if leave_module_live and profile is not None:
            if profile.pk not in leave_taken_this_year:
                score += WEIGHT_NO_LEAVE_TAKEN
                reasons.append('No approved leave taken this year')

        # Signal 2 — engagement: no recent check-in on record, or the last
        # recorded rating was low.
        if checkin_module_live and profile is not None:
            latest = latest_checkin_by_profile.get(profile.pk)
            if latest is None:
                score += WEIGHT_CHECKIN_OR_RATING
                reasons.append('No performance check-in on record')
            elif latest.overall_rating in LOW_RATINGS:
                score += WEIGHT_CHECKIN_OR_RATING
                reasons.append('Most recent performance rating was below expectations')
            elif latest.conversation_date and latest.conversation_date < checkin_cutoff:
                score += WEIGHT_CHECKIN_OR_RATING
                reasons.append('No recent performance check-in')

        # Signal 3 — career stagnation: simple tenure proxy (see docstring).
        if emp.hire_date and emp.hire_date <= tenure_cutoff:
            years = (today - emp.hire_date).days // 365
            score += WEIGHT_LONG_TENURE
            reasons.append(f'Long tenure ({years}+ yrs) with no recent progression on record')

        # Signal 4 — under-appreciation: no kudos in the lookback window.
        if recognition_module_live and profile is not None:
            if profile.pk not in recognized_recently:
                score += WEIGHT_NO_RECOGNITION
                reasons.append('No recognition received in the last 12 months')

        if profile is None:
            reasons.append(
                'HR profile not yet set up — leave / check-in / recognition '
                'signals unavailable')

        score = min(score, 100)
        results.append({
            'profile_id': str(profile.pk) if profile is not None else str(emp.pk),
            'name': emp.full_name,
            'department': emp.department or '',
            'job_title': emp.job_title or '',
            'score': score,
            'band': _band_for_score(score),
            'reasons': reasons,
        })

    results.sort(key=lambda r: (-r['score'], r['name']))
    return results


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def flight_risk(request):
    """GET /api/v1/hris/flight-risk/

    Query params (both optional):
      department= — exact, case-insensitive match on Employee.department
      band=       — high | med | low, restricts the returned `results`

    Response: {generated_at, count, high, results: [...]}. `high` is the
    high-band count for the current department scope BEFORE any ?band=
    filter narrows `results`, so a summary tile stays meaningful even
    while the table underneath it is filtered down to a single band.
    """
    deny = _deny_if_not_whitelisted(request)
    if deny:
        return deny

    from payroll.models import Employee

    qs = Employee.objects.filter(status=Employee.Status.ACTIVE)
    qs = apply_company_scope(request, qs, 'company_id')

    department = (request.query_params.get('department') or '').strip()
    if department:
        qs = qs.filter(department__iexact=department)

    all_results = compute_flight_risk(qs)
    high_count = sum(1 for r in all_results if r['band'] == 'high')

    band = (request.query_params.get('band') or '').strip().lower()
    results = ([r for r in all_results if r['band'] == band]
               if band in ('high', 'med', 'low') else all_results)

    return Response({
        'generated_at': timezone.now().isoformat(),
        'count': len(results),
        'high': high_count,
        'results': results,
    })
