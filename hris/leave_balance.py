"""
hris/leave_balance.py — single source of truth for an employee's leave
balances.

Extracted from feature_views.leave_balances (CFO 2026-07-14) so the leave
API AND the leave-approval email compute the exact same numbers. Duplicating
the accrual / opening-balance maths in the email builder would have drifted
the moment either changed — one function, one truth.

`balances_for_profile(profile)` returns the same list the /hris/api/leave-
balances/ endpoint returns, so the email can render annual-days-available,
sick-remaining, etc. straight from it.
"""
from __future__ import annotations

import datetime as _dt
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from django.utils import timezone

from hris.models import LeaveRequest


def days_out(value: float) -> float:
    """A leave-day figure as it is shown to staff: two decimals, never rounded UP.

    EXCO change request (2026-08-11). One twelfth of a 21-day entitlement is
    1.75 and every screen showed 1.8 — the report was claiming a fraction of a
    day more leave than the completed months had actually earned. Rounding a
    leave figure up credits days nobody has worked for, so this truncates.

    TWO decimals, not one: truncating 1.75 to one decimal shows 1.7, which is
    further from the truth than the 1.8 being corrected. 1.75 is exact at two.

    ROUND_FLOOR, not ROUND_DOWN: ROUND_DOWN truncates toward zero, which on a
    negative balance (an over-taken position) would move the figure UP, toward
    the employee. Floor always moves away from more leave, whatever the sign.
    """
    return float(Decimal(str(value)).quantize(Decimal('0.01'),
                                              rounding=ROUND_FLOOR))


# Annual entitlements the Conditions of Service actually grant (§7.1) — the only
# values an uploaded entitlement column should ever hold. On prod the June-2026
# tracker load matches this on 64 of 90 annual rows (18 × 51, 25 × 8, 21 × 5);
# the other 26 hold the year's total credit instead, which is why a row outside
# this set is marked for HR rather than divided by twelve.
COS_ANNUAL_ENTITLEMENTS = frozenset({18.0, 21.0, 25.0})
COS_ANNUAL_ENTITLEMENTS_TEXT = ', '.join(
    f'{d:g}' for d in sorted(COS_ANNUAL_ENTITLEMENTS))


def _last_day_of_month(d: _dt.date) -> bool:
    return (d + _dt.timedelta(days=1)).month != d.month


def completed_months(as_at: _dt.date, since: _dt.date | None = None) -> int:
    """Whole calendar months FINISHED on or before *as_at*, counting from
    *since* (default: the start of as_at's calendar leave year).

    CoS §7.5.1 accrues annual leave monthly and ELRA s.219 says leave accrues
    progressively, so a month is credited when it has been WORKED — on its last
    day — not on its first. The old code used `timezone.now().month` directly,
    which credited the whole of August on 1 August: an employee resigning on
    15 August was shown August's accrual for a month they had not completed.
    HR reported this (Ontlametse Mogomotsi, ref AD/HR/IA/2026/001).

    31 Aug → 8 (August now complete) · 1 Aug → 7 (July only) ·
    1 Jan → 0 (nothing accrued yet) · 31 Dec → 12 (a full year).
    """
    if since is None:
        since = _dt.date(as_at.year, 1, 1)
    if as_at < since:
        return 0
    months = (as_at.year - since.year) * 12 + (as_at.month - since.month)
    # The month containing as_at counts only once it has actually finished.
    if _last_day_of_month(as_at):
        months += 1
    # A part-month at the START (since is mid-month) is not a completed month
    # either: nothing accrues until the first whole month after it.
    if since.day != 1:
        months -= 1
    return max(0, months)


def accrued_to_date(entitlement_days: float, as_at: _dt.date,
                    since: _dt.date | None = None) -> float:
    """Annual entitlement earned by *as_at* — one twelfth per completed month."""
    return days_out(entitlement_days * completed_months(as_at, since) / 12.0)


def accrual_cutoff(profile, as_at: _dt.date | None = None) -> _dt.date:
    """The last date leave may accrue to for this person.

    Nobody accrues leave after they have left. Oprah Mogomotsi's spot-check
    (2026-08-10) found three leavers still gaining days every month — and while
    those three are a DATA problem (HR has not recorded a leaving date, so the
    system still reads them as active), the code gap is real and applies to the
    eleven employees who ARE properly marked terminated.

    Before the month-end accrual fix this was hidden: an uploaded balance never
    moved, so a leaver's figure sat still by accident. Now that accrual is live it
    has to be stopped deliberately.
    """
    from django.utils import timezone
    as_at = as_at or timezone.localdate()
    emp = getattr(profile, 'employee', None)
    if emp is None:
        return as_at
    term = getattr(emp, 'termination_date', None)
    if term and term < as_at:
        return term
    return as_at


def accrual_start(profile, year_start: _dt.date) -> _dt.date:
    """The first date leave may accrue from for this person.

    The twin of `accrual_cutoff`. Nobody accrues leave before they were hired,
    and until now this engine never asked: `accrued_to_date` was called with no
    `since`, so it defaulted to 1 January and credited every employee a full
    year-to-date no matter when they actually joined.

    Found 2026-09-07 (CFO). A Client Onboarding Intern who joined in August
    showed 14.00 annual days available - 21 / 12 x 8 months counted from
    1 January - and an encashment for all 14 was raised and valued against it.
    She was not the outlier: 84 active employees were reading a balance accrued
    from 1 January, among them a developer whose fourth day of service was the
    day this was found and who was likewise shown 14.00 days.

    The sister engine on the HR leave report (`feature_views._accrual_months`)
    has bounded by hire date since 6 Aug 2026, so the two screens have been
    disagreeing for a month; this closes that gap rather than inventing a new
    rule. `completed_months` already refuses to credit a part-month at the
    start, so a mid-month joiner earns nothing for their joining part-month -
    the conservative reading of CoS 7.5.1, and the same arithmetic already
    applied to an uploaded opening balance's as-at date.

    A missing hire date falls back to *year_start*, exactly as before - the
    figure is then an assumption, not a fact, which is why
    `leave_encash_service.apply_encashment` refuses to value an encashment for
    anyone whose hire date is blank.
    """
    emp = getattr(profile, 'employee', None) if profile is not None else None
    hire = getattr(emp, 'hire_date', None) if emp is not None else None
    if hire and hire > year_start:
        return hire
    return year_start


def balances_for_profile(profile) -> list[dict[str, Any]]:
    """Per-type entitlement / accrued / used / available for one profile.

    profile may be None (unlinked account) → returns the rule set with zero
    usage so callers can still render every leave type. Mirrors the CoS rules
    exactly as the self-service balances page does.
    """
    # Imported lazily to avoid a circular import (feature_views imports this
    # module at call time via balances_for_profile).
    from hris.feature_views import get_leave_rules

    year_start = _dt.date(timezone.localdate().year, 1, 1)

    used_per_code: dict[str, float] = {}
    if profile is not None:
        qs = (LeaveRequest.objects
              .filter(profile=profile, start_date__gte=year_start,
                      status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.PENDING])
              .select_related('leave_type'))
        for lr in qs:
            code = (lr.leave_type.code or lr.leave_type.name or '').lower().strip()
            used_per_code[code] = used_per_code.get(code, 0.0) + float(lr.days or 0)

    # Accrual + carry-over (pattern adopted from the timeoff-management
    # reference repo, 2026-06-09). CoS §7.5.1: annual leave accrues monthly —
    # mirror the apply-time rule (entitlement × months elapsed / 12) so the
    # displayed "available" matches what can actually be booked. Other CoS
    # types are available-on-need (full entitlement). Carry-over cap = §7.2
    # (2× annual). Fields are ADDITIVE — days/used/remaining kept for back-compat.
    # Months COMPLETED, not the current month number. `timezone.now().month`
    # credited the whole of the running month on its first day (HR ref
    # AD/HR/IA/2026/001) — see completed_months().
    # …and never past the employee's last day of service (Oprah 2026-08-10).
    today_for_accrual = accrual_cutoff(profile) if profile is not None \
        else timezone.localdate()
    # ...and never BEFORE the day they joined (CFO 2026-09-07). See accrual_start.
    accrual_from = accrual_start(profile, year_start)
    emp_gender = (profile.gender or '').strip() if profile is not None else ''

    # Bug 9c0aa7d3: HR can bulk-upload corrected as-at-date opening positions.
    # If a LeaveOpeningBalance exists for (profile, code) with as_at_date <= today,
    # use the LATEST one as the baseline instead of the year-start accrual.
    # ADDITIVE: codes/profiles with no uploaded opening keep the legacy maths,
    # so nobody's displayed balance changes unless HR uploaded a correction.
    opening_by_code: dict = {}
    if profile is not None:
        from hris.models import LeaveOpeningBalance
        today_d = timezone.localdate()
        # ORDER MATTERS, AND SO DOES THE TIE-BREAK. Ordering by as_at_date alone left the
        # winner UNDEFINED when HR re-uploaded a correction for the SAME as-at date: Postgres
        # returned the rows in arbitrary order and the OLD figure could win. That is bug
        # c2888ba7 — HR uploaded corrected balances as at 30 June, the upload reported
        # success, and the screen kept showing the old numbers. 63 (profile, type, date)
        # combinations were colliding; one employee was demonstrably reading the 11 July
        # row instead of the 5 August one. `-created_at` makes the most recently uploaded row
        # win a tie, which is what "I am correcting this" means.
        for ob in (LeaveOpeningBalance.objects
                   .filter(profile=profile, as_at_date__lte=today_d)
                   .order_by('leave_type_code', '-as_at_date', '-created_at')):
            opening_by_code.setdefault((ob.leave_type_code or '').lower().strip(), ob)

    # Leave encashment (CFO 2026-07-21): days are RESERVED against the balance
    # the moment an application is raised and stay reserved through the whole
    # approval chain and payment — otherwise a pending application's days could
    # be double-spent as booked leave. Only a REJECTED application releases them.
    # NO year filter (Fable 5 review 2026-07-21): a year filter let an
    # application straddling 1 Jan — or even a PAID one — stop reserving days in
    # the next calendar year, reopening the double-spend. Every non-rejected row
    # reserves; the opening-balance path below still bounds by as_at_date so
    # encashments already baked into an uploaded opening aren't double-counted.
    #
    # ...but a SETTLED row is not a reservation. 'paid' rode in on the same
    # tuple with no date filter at all, so a 10-day encashment PAID in March
    # 2024 still removed 10 of the 14 days accrued by Sep 2026 — and would
    # have gone on doing so for ever, against a balance that restarts every
    # January (2026-09-20). Only the settled state is bounded, and it is
    # bounded the way the opening-balance path below already bounds them: by
    # the date the application was raised. Every IN-FLIGHT state (pending_cfo,
    # pending_hr, pending_finance, approved) still reserves without a year
    # filter — that is the straddling-application rule above, unchanged.
    encashed_rows: list = []
    if profile is not None:
        from hris.leave_encash_models import LeaveEncashment
        encashed_rows = [
            e for e in LeaveEncashment.objects.filter(
                profile=profile,
                status__in=LeaveEncashment.OPEN_STATUSES)
            if e.status != LeaveEncashment.Status.PAID
            or timezone.localtime(e.created_at).date() >= year_start
        ]
    encashed_per_code: dict[str, float] = {}
    for e in encashed_rows:
        c = (e.leave_type_code or '').lower().strip()
        encashed_per_code[c] = encashed_per_code.get(c, 0.0) + float(e.days or 0)

    balances: list[dict[str, Any]] = []
    for code, rule in get_leave_rules().items():
        # Gender-locked types (maternity → F, paternity → M) only show for the
        # matching gender. If gender isn't recorded we still show it; apply_leave
        # then blocks and tells HR to set the gender. (Bug 77137f16.)
        greq = rule.get('gender')
        if greq and emp_gender and emp_gender != greq:
            continue
        used = used_per_code.get(code, 0.0)

        ob = opening_by_code.get(code)
        if ob is not None:
            # Uploaded opening is correct AS AT ob.as_at_date; deduct leave taken
            # since then. (Bug 9c0aa7d3.)
            used_since = 0.0
            for lr in LeaveRequest.objects.filter(
                    profile=profile, leave_type__code__iexact=code,
                    start_date__gte=ob.as_at_date,
                    status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.PENDING]):
                used_since += float(lr.days or 0)
            # Encashments raised before as_at_date are already baked into the
            # uploaded opening figure — only reserve ones raised since.
            encashed_since = sum(
                float(e.days or 0) for e in encashed_rows
                if (e.leave_type_code or '').lower().strip() == code
                and timezone.localtime(e.created_at).date() >= ob.as_at_date)
            # `entitlement_days` and `accrued_days` are NOT nullable — they
            # default to 0 — so `x or fallback` never guards missing data. It
            # only ever fires on a REAL zero, which is the defect Oprah
            # Mogomotsi reported (ref 3c45e603).
            #
            # Entitlement: a stored 0 means the upload column was left blank —
            # nobody has an entitlement of nothing — so the policy default
            # still stands in.
            entitlement = (float(ob.entitlement_days) if ob.entitlement_days
                           else float(rule['days']))

            # Accrued: this is the one that mattered. `or entitlement` handed
            # anyone with nothing accrued their WHOLE year. Fixed by using the
            # same rule the non-uploaded branch below already uses: only annual
            # leave accrues month by month; sick, study, compassionate and
            # special are granted in full and do not accrue at all.
            #
            # That distinction is why the naive fix would have been worse than
            # the bug: of the 416 uploaded rows carrying a zero, 413 are
            # non-accruing types that are SUPPOSED to show their full
            # entitlement. Only 3 annual rows were genuinely misreporting.
            if code == 'annual':
                # ACCRUE FORWARD from the uploaded position. The docstring on
                # LeaveOpeningBalance always said the engine "accrues forward
                # from there", but the code used ob.accrued_days verbatim — so
                # for every employee HR uploaded (which is nearly everyone) the
                # accrued figure never moved again. That is the "OMNI has no
                # live monthly accrual engine" report: correct, and this is it.
                # One twelfth per whole month completed since the as-at date.
                earned_since = accrued_to_date(entitlement, today_for_accrual,
                                               since=max(ob.as_at_date,
                                                         accrual_from))
                accrued = days_out(float(ob.accrued_days) + earned_since)
            else:
                accrued = entitlement                 # granted in full, never accrued
                earned_since = 0.0
            available = max(0.0, float(ob.opening_balance_days) + earned_since
                            - used_since - encashed_since)
            balances.append({
                'code':          code,
                'name':          code.capitalize() + ' Leave',
                'days':          entitlement,
                'accrued':       days_out(accrued),
                'used':          days_out(used_since),
                'encashed':      days_out(encashed_since),
                'available':     days_out(available),
                'remaining':     days_out(available),
                'carry_over_cap': entitlement * 2,
                'accrues':       (code == 'annual'),
                'accrued_since_upload': days_out(earned_since),
                'paid_pct':      rule['paid_pct'],
                'cos':           rule['cos'],
                'rule':          rule['rule'],
                'as_at':         ob.as_at_date.isoformat(),
                'source':        'opening_balance',
            })
            continue

        accrues = (code == 'annual')
        accrued = (accrued_to_date(float(rule['days']), today_for_accrual,
                                   since=accrual_from)
                   if accrues else float(rule['days']))
        encashed = encashed_per_code.get(code, 0.0)
        available = max(0.0, accrued - used - encashed)
        balances.append({
            'code':          code,
            'name':          code.capitalize() + ' Leave',
            'days':          rule['days'],            # full annual entitlement
            'accrued':       days_out(accrued),       # accrued to date
            'used':          days_out(used),
            'encashed':      days_out(encashed),
            'available':     days_out(available),     # bookable now (accrued − used − encashed)
            'remaining':     max(0.0, rule['days'] - used),  # back-compat
            'carry_over_cap': rule['days'] * 2,        # §7.2 max accumulation
            'accrues':       accrues,
            'paid_pct':      rule['paid_pct'],
            'cos':           rule['cos'],
            'rule':          rule['rule'],
            'source':        'accrual',
        })
    return balances
