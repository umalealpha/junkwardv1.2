"""
hris/eligibility.py

Reconciliation layer for the Workforce Brief / Exceptions (CFO 2026-07-14).

Only PAYROLL staff who are expected to track time should be emailed or reported.

CFO directive 2026-07-18: ANYONE on salary with a payslip in omni is tracked —
there is NO seniority / role / keyword immunity. This replaced the earlier
keyword-exemption for agents/interns/consultants. (The keyword helpers below are
retained only for their unit tests; they no longer decide who is tracked.)

CFO directive 2026-08-01: an EXPLICIT per-person don't-track directive is
honoured again — the track / don't-track buttons on the Time Doctor setup page
(CFO / Arun / Arjun only) had been writing directive(False) rows that nothing
read. There is still no automatic immunity: only a deliberate click exempts
someone, and it is attributed (updated_by) and visible on the roster.

Tracked  = active HRISProfile employee WITH a payslip, OR an explicit
           TrackingDirective(True) force-tracking a genuinely-working employee
           whose payslip isn't loaded in omni yet.
Excluded = an explicit TrackingDirective(False); no payslip and no force-track
           directive; terminated/suspended (Employee.status not active);
           non-payroll TD users (no HRISProfile); and anyone on APPROVED leave
           for the day (per-day via on_leave_names).

This is the HRIS link: it reads payroll.Employee.status + hris.LeaveRequest.
"""
from __future__ import annotations

import datetime
import logging
import re

from django.conf import settings

log = logging.getLogger(__name__)

# STRICT whole words only — plurals are listed explicitly because a
# plural-tolerant 'commission(s)' would re-catch "Commissions Administrator",
# the exact false exclusion this fixes.
DEFAULT_EXCLUDE_KEYWORDS = ['agent', 'agents', 'broker', 'brokers', 'independent',
                            'commission', 'consultant', 'consultants', 'intern', 'interns']


def _exclude_patterns():
    """Whole-word regexes for the exclusion keywords, so 'intern' matches
    "Intern" / "Interns" but never "Internal Audit"."""
    kws = [k.strip().lower()
           for k in getattr(settings, 'WORKFORCE_EXCLUDE_KEYWORDS', DEFAULT_EXCLUDE_KEYWORDS) if k]
    return [re.compile(r'\b' + re.escape(k) + r'\b') for k in kws]


def _exclude_emails():
    return {e.strip().lower() for e in getattr(settings, 'WORKFORCE_EXCLUDE_EMAILS', []) if e and e.strip()}


def _directive_map():
    """{employee_id: expected_to_track} from explicit CFO/HR directives."""
    try:
        from hris.models import TrackingDirective
        return {d.employee_id: d.expected_to_track for d in TrackingDirective.objects.all()}
    except Exception:    # noqa: BLE001
        return {}


def _paid_employee_ids() -> set:
    """Employee ids with a recent payslip — the payroll test.

    CFO rule 2026-09-10: no payslip in the last ~1 month (45 days) = NOT
    tracked. 45 days instead of 30 gives a 2-week grace for late payroll
    runs. (Was: any payslip ever. Tightened because contractors like Kamlesh
    stayed in TD reports 3 months after their last pay.) An explicit
    TrackingDirective(expected=True) still force-tracks; a directive always
    wins over this default."""
    try:
        from datetime import timedelta
        from django.utils import timezone
        from payroll.models import Payslip
        cutoff = timezone.now().date() - timedelta(days=45)
        return set(
            Payslip.objects
            .filter(period__end_date__gte=cutoff)
            .values_list('employee_id', flat=True)
            .distinct()
        )
    except Exception:    # noqa: BLE001
        return set()


def _keyword_expected(e, pats, ex) -> bool:
    """The keyword/role GUESS: is this employee expected to track? (before any
    explicit directive)."""
    email = (getattr(e, 'email', '') or '').strip().lower()
    blob = f"{getattr(e, 'department', '') or ''} {getattr(e, 'job_title', '') or ''}".lower()
    if email and email in ex:
        return False
    if any(pat.search(blob) for pat in pats):
        return False
    return True


def tracking_profiles():
    """Active payroll HRIS employees expected to track time.

    CFO directive 2026-07-18: ANYONE on salary with a payslip is tracked — no
    seniority / role / keyword immunity (this replaced the old keyword-exemption
    for agents/interns/consultants). A directive(True) still force-tracks a
    genuinely-working employee whose payslip isn't loaded in omni yet, and a
    deliberate directive(False) exempts one person (CFO 2026-08-01).
    On-leave is applied per-day by the caller."""
    from hris.models import HRISProfile
    directive = _directive_map()
    paid = _paid_employee_ids()
    out = []
    for p in (HRISProfile.objects.select_related('employee')
              .filter(employee__status='active').order_by('employee__full_name')):
        e = p.employee
        if directive.get(e.id) is False:
            continue
        if e.id in paid or directive.get(e.id) is True:
            out.append(p)
    return out


def tracking_roster():
    """Every active payroll employee with their EFFECTIVE tracking status and
    where it came from — powers the who-tracks setup dashboard (CFO 2026-07-14).
    Returns dicts: {employee_id, name, department, job_title, expected, source}."""
    from hris.models import HRISProfile
    directive = _directive_map()
    paid = _paid_employee_ids()
    rows = []
    for p in (HRISProfile.objects.select_related('employee')
              .filter(employee__status='active').order_by('employee__full_name')):
        e = p.employee
        # CFO 2026-08-01: an explicit don't-track click wins over the payslip
        # rule; otherwise a payslip => tracked (CFO 2026-07-18, no auto immunity).
        if directive.get(e.id) is False:
            expected, source = False, 'directive'
        elif e.id in paid:
            expected, source = True, 'payslip'
        elif directive.get(e.id) is True:
            expected, source = True, 'directive'
        else:
            expected, source = False, 'no-payslip'
        rows.append({'employee_id': e.id, 'name': (e.full_name or '').strip(),
                     'department': e.department or '', 'job_title': e.job_title or '',
                     'expected': expected, 'source': source})
    return rows


def eligible_name_set():
    """Lower-cased full names of the tracking-eligible payroll staff."""
    return {(getattr(p.employee, 'full_name', '') or '').strip().lower()
            for p in tracking_profiles()
            if (getattr(p.employee, 'full_name', '') or '').strip()}


def on_leave_names(day: datetime.date) -> set:
    """Lower-cased full names on APPROVED leave covering `day`, plus anyone whose
    payroll status is 'on_leave'. Used to exclude them from tracking exceptions
    and to list them for managers.

    Failures are LOGGED LOUDLY (a silent miss here flags someone on leave as
    "did not track" — exactly the false accusation this feature must not make)
    but never raise: a broken leave lookup degrades to the circuit breaker
    catching the inflated did-not-track count."""
    names = set()
    try:
        from hris.models import LeaveRequest
        rows = (LeaveRequest.objects
                .filter(status=LeaveRequest.Status.APPROVED, start_date__lte=day, end_date__gte=day)
                .select_related('profile__employee'))
        for lr in rows:
            nm = (getattr(getattr(lr.profile, 'employee', None), 'full_name', '') or '').strip().lower()
            if nm:
                names.add(nm)
    except Exception:    # noqa: BLE001
        log.exception('on_leave_names: LeaveRequest lookup FAILED for %s — '
                      'people on leave may be wrongly reported as did-not-track', day)
    try:
        from payroll.models import Employee
        for e in Employee.objects.filter(status='on_leave'):
            nm = (e.full_name or '').strip().lower()
            if nm:
                names.add(nm)
    except Exception:    # noqa: BLE001
        log.exception('on_leave_names: payroll on_leave lookup FAILED for %s', day)
    return names


def on_leave_employee_pks(day: datetime.date) -> set:
    """Employee PKs on approved leave covering `day` — immune to name mismatches."""
    pks = set()
    try:
        from hris.models import LeaveRequest
        for lr in (LeaveRequest.objects
                   .filter(status=LeaveRequest.Status.APPROVED,
                           start_date__lte=day, end_date__gte=day)
                   .select_related('profile__employee')):
            emp = getattr(lr.profile, 'employee', None)
            if emp:
                pks.add(emp.pk)
    except Exception:  # noqa: BLE001
        log.exception('on_leave_employee_pks: lookup FAILED for %s', day)
    try:
        from payroll.models import Employee
        pks.update(Employee.objects.filter(status='on_leave').values_list('pk', flat=True))
    except Exception:  # noqa: BLE001
        log.exception('on_leave_employee_pks: payroll lookup FAILED for %s', day)
    return pks
