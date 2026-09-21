"""
hris/manager_scorecard_views.py — Manager Scorecard (CFO/HR accountability).

Turns data already captured elsewhere in the HRIS into a per-manager
accountability view: is this manager actually running monthly check-ins and
Development Dialogues with their team, and is their team shrinking? Built
entirely on hris.performance_feedback_models.MonthlyCheckIn (the ELRA
monthly check-in) and hris.talent_cockpit_models.DevelopmentDialogue (the
Talent Cockpit) — no new model, no new writes, nothing this module can
corrupt.

Whitelist-only (HR / CFO / exec) — the same two-layer gate as hris/api_views
.employees / .grades. Deliberately NOT entity/company-scoped: the audience
for this report already sees across the whole group (this mirrors `grades`,
a catalogue-style endpoint, rather than `employees`, which clamps to
UserCompanyAccess) — and compute_manager_scorecards() is a pure, argument-
free helper by design, so per-caller scoping belongs in a future view, not
smuggled into the helper's signature.
"""
from __future__ import annotations

import datetime as dt

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.api_views import _deny_if_not_whitelisted
from hris.models import HRISProfile
from hris.performance_feedback_models import MonthlyCheckIn
from hris.talent_cockpit_models import DevelopmentDialogue
from payroll.models import Employee

# ── Tunable policy constants (HR/CFO judgment calls, not statute) ──────────
# "This month" mirrors hris.perf_monthly_views.manager_league — the current
# calendar month, not last-completed, so the scorecard is watched live as
# the month builds up (same convention as the existing league table).
ATTRITION_WINDOW_DAYS = 365          # "left in the last ~12 months"
DIALOGUE_WINDOW_DAYS = 365           # "a dialogue in a sensible recent window"
CHECKIN_WEIGHT = 0.6                 # score blend — see compute_manager_scorecards()
DIALOGUE_WEIGHT = 0.4
ATTRITION_PENALTY_PER_LEAVER = 15.0  # points knocked off score per report lost


def _current_period() -> tuple[int, int, str]:
    """(year, month, human label) for 'this month', e.g. (2026, 7, 'July 2026')."""
    today = timezone.localdate()
    return today.year, today.month, today.strftime('%B %Y')


def _attrition_is_derivable() -> bool:
    """payroll.Employee.termination_date is optional. If it has never been
    populated on an actual exit anywhere in the dataset, we cannot honestly
    say WHEN someone left, so we cannot claim a '12 months' window for
    anyone — callers get None instead of a guessed number."""
    return Employee.objects.filter(
        status=Employee.Status.TERMINATED,
    ).exclude(termination_date__isnull=True).exists()


def compute_manager_scorecards() -> list[dict]:
    """One row per manager (payroll.Employee) who currently has ≥1 active
    (non-terminated) direct report, i.e. an HRISProfile with manager=<them>.

    Per-manager fields:
      reports       — active direct-report headcount (the % denominator below).
      checkin_pct   — % of those reports with a MonthlyCheckIn for the
                       current calendar month.
      dialogue_pct  — % of those reports with a DevelopmentDialogue that is
                       both the live period (is_current=True) AND touched
                       within the last DIALOGUE_WINDOW_DAYS — matched by
                       employee email (DevelopmentDialogue.employee is an
                       optional link; email is the reliable join used
                       throughout hris.talent_cockpit_views).
      attrition     — reports (any, including ones no longer "active" above)
                       who are TERMINATED with a termination_date inside the
                       last ATTRITION_WINDOW_DAYS. None when termination_date
                       isn't populated anywhere in the dataset (not derivable
                       — see _attrition_is_derivable) rather than guessed. A
                       TERMINATED report with no termination_date on file is
                       excluded from the count either way (can't place them
                       in the window), so this is a safe floor, never inflated.

    SCORE FORMULA (single source of truth — tune the constants above, not
    the shape below):
        base  = CHECKIN_WEIGHT(0.6) × checkin_pct + DIALOGUE_WEIGHT(0.4) × dialogue_pct
        score = clamp(base − ATTRITION_PENALTY_PER_LEAVER(15) × attrition, 0, 100)
    Check-ins outweigh dialogues because they are the monthly, higher-
    frequency accountability signal; the Development Dialogue is the deeper
    annual/bi-annual review. Each report lost in the last 12 months knocks a
    flat 15 points off an otherwise-perfect base — attrition is the sharpest
    signal this scorecard carries, so it is subtracted, not blended in
    proportionally. No penalty is applied (score = base) when attrition
    isn't derivable.

    Returned sorted by score ASCENDING (worst first) — so HR sees who needs
    help before anyone else.
    """
    year, month, _label = _current_period()
    today = timezone.localdate()
    cutoff_attr = today - dt.timedelta(days=ATTRITION_WINDOW_DAYS)
    # cutoff_dlg is compared against DevelopmentDialogue.updated_at, a
    # DateTimeField — so it must be a timezone-aware datetime, not a bare date,
    # or Django warns (and, with USE_TZ strict, errors) on the naive compare.
    # cutoff_attr stays a date: it filters termination_date, a DateField.
    cutoff_dlg = timezone.now() - dt.timedelta(days=DIALOGUE_WINDOW_DAYS)
    attrition_derivable = _attrition_is_derivable()

    # Active (non-terminated) direct reports — mirrors the `scoped` queryset
    # in hris.perf_monthly_views.manager_league.
    active_profiles = (HRISProfile.objects
                       .select_related('employee')
                       .exclude(employee__status=Employee.Status.TERMINATED))
    mgr_ids = (active_profiles.exclude(manager=None)
               .values_list('manager', flat=True).distinct())

    # One pass to build the "has a live, recently-touched dialogue" email
    # set — avoids re-querying DevelopmentDialogue once per manager. Emails
    # are compared lower-cased on both sides (DevelopmentDialogue.email is
    # free-typed, not guaranteed lower-case on write).
    recent_dialogue_emails = {
        e.lower() for e in DevelopmentDialogue.objects
        .filter(is_current=True, updated_at__gte=cutoff_dlg)
        .exclude(email='')
        .values_list('email', flat=True)
    }

    rows: list[dict] = []
    for mgr in Employee.objects.filter(pk__in=list(mgr_ids)):
        reports = active_profiles.filter(manager=mgr)
        total = reports.count()
        if not total:
            continue

        # auto_posted=False only: a month Omni wrote for a silent manager must
        # not score as the manager having complied (Fable, 2026-08-26).
        given = MonthlyCheckIn.objects.filter(
            profile__in=reports, period_year=year, period_month=month,
            auto_posted=False).count()
        auto_posted = MonthlyCheckIn.objects.filter(
            profile__in=reports, period_year=year, period_month=month,
            auto_posted=True).count()
        checkin_pct = round(100 * given / total, 1)

        report_emails = [e for e in reports.values_list('employee__email', flat=True) if e]
        dialogued = sum(1 for e in report_emails if e.lower() in recent_dialogue_emails)
        dialogue_pct = round(100 * dialogued / total, 1)

        attrition = None
        if attrition_derivable:
            # ALL reports ever linked to this manager, not just the active
            # set above — a report who left stays linked via HRISProfile
            # (manager is only cleared if the MANAGER's own row is deleted).
            attrition = (HRISProfile.objects.filter(manager=mgr)
                        .filter(employee__status=Employee.Status.TERMINATED,
                                employee__termination_date__gte=cutoff_attr,
                                employee__termination_date__lte=today)
                        .count())

        base = CHECKIN_WEIGHT * checkin_pct + DIALOGUE_WEIGHT * dialogue_pct
        penalty = ATTRITION_PENALTY_PER_LEAVER * (attrition or 0)
        score = round(max(0.0, min(100.0, base - penalty)), 1)

        rows.append({
            'manager_id': str(mgr.pk),
            'manager_name': mgr.full_name,
            'department': mgr.department,
            'reports': total,
            'checkin_pct': checkin_pct,
            'auto_posted': auto_posted,
            'dialogue_pct': dialogue_pct,
            'attrition': attrition,
            'score': score,
        })

    rows.sort(key=lambda r: (r['score'], r['manager_name']))
    return rows


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def manager_scorecard(request):
    """GET /api/v1/hris/manager-scorecard/ — HR / CFO / exec whitelist only."""
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied
    _year, _month, label = _current_period()
    return Response({
        'generated_at': timezone.now().isoformat(),
        'month_label': label,
        'managers': compute_manager_scorecards(),
    })
