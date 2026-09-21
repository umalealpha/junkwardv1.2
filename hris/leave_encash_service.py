"""
hris/leave_encash_service.py — valuation, the 4-step approval chain and the
provision register for leave encashment (CFO directive 2026-07-21).

Chain: employee applies → CFO → HR → (FC or FM) → paid.

Stage authority:
  * apply    — ANY employee, for THEMSELVES (self-service). Amount is computed
               server-side from their latest payslip BASIC; never typed.
  * CFO      — the CFO (pganesharajah / Title.CFO / superuser backup).
  * HR       — Head of Human Capital / HR team (Title.HR_MANAGER, or the
               HR_MANAGER / HRIS role, or Unami). Deliberately NOT the finance
               titles (which map to hris_role 'hr').
  * Finance  — Financial Controller OR Finance Manager (either one signs).
  * paid     — Finance, once the payout is loaded for payment.

Segregation of duties: the applicant never approves their own; and nobody who
signed an earlier leg may sign a later one (so one superuser can't rubber-stamp
the whole chain). The balance is re-checked at every approval — leave taken (or
another application) since the apply can shrink it.
"""
from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.hris_access import _local_part
from .amendment_service import CFO_EMAIL, UNAMI_EMAIL, _local
from .leave_encash_models import WORKING_DAYS_PER_MONTH, LeaveEncashment

log = logging.getLogger(__name__)

TWO_PLACES = Decimal('0.01')


# ─── Stage authority ───────────────────────────────────────────────────────────

def _title(user):
    from core.models import get_user_profile
    prof = get_user_profile(user)
    return prof.title if (prof and prof.is_active) else None


def is_cfo(user) -> bool:
    from core.models import UserProfile
    if _local_part(getattr(user, 'email', '')) == _local(CFO_EMAIL):
        return True
    if _title(user) == UserProfile.Title.CFO:
        return True
    return bool(getattr(user, 'is_superuser', False))


def is_hr(user) -> bool:
    """Real HR — NOT the finance leadership that hris_role folds into 'hr'."""
    from core.models import UserProfile, UserRoleAssignment
    if _title(user) == UserProfile.Title.HR_MANAGER:
        return True
    if _local_part(getattr(user, 'email', '')) == _local(UNAMI_EMAIL):
        return True
    try:
        if UserRoleAssignment.objects.filter(
                user=user, role__code__in=['HR_MANAGER', 'HRIS']).exists():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def is_finance_approver(user) -> bool:
    """Financial Controller OR Finance Manager — either one may sign."""
    from core.models import UserProfile
    if _title(user) in (UserProfile.Title.FINANCIAL_CONTROLLER,
                        UserProfile.Title.FINANCE_MANAGER):
        return True
    return bool(getattr(user, 'is_superuser', False))


def can_pay(user) -> bool:
    return is_finance_approver(user)


def can_view_all(user) -> bool:
    """Who sees the whole queue + provision register (salary data)."""
    return is_cfo(user) or is_hr(user) or is_finance_approver(user)


# ─── Valuation ────────────────────────────────────────────────────────────────

def latest_basic_line_for(employee):
    """The most recent PayslipLine carrying a positive BASIC for *employee*,
    or None. Both the valuation (BASIC) and the tax base (that same payslip's
    taxable gross) are read off this ONE payslip, so they can never come from
    two different months.

    Gate on CONTENT, not payslip status — prod payslips sit in 'draft' with real
    figures (see reference_payslip_status_draft). Cancelled slips are excluded;
    PayslipLine amounts are always BWP (they drive gross_amount).
    """
    from payroll.models import Payslip, PayslipLine

    return (PayslipLine.objects
            .filter(payslip__employee=employee,
                    component__code__iexact='BASIC',
                    amount__gt=0)
            .exclude(payslip__status=Payslip.Status.CANCELLED)
            .select_related('payslip__period')
            .order_by('-payslip__period__start_date')
            .first())


def latest_basic_for(employee) -> tuple[Decimal, str]:
    """(monthly BASIC in BWP, source period name) from the employee's most
    recent payslip carrying a positive BASIC line."""
    line = latest_basic_line_for(employee)
    if line is None:
        return Decimal('0.00'), ''
    return line.amount, line.payslip.period.period_name


def daily_rate_for(basic: Decimal) -> Decimal:
    """BASIC ÷ 24 working days (CFO 2026-08-03, was 22 — BASIC only, no allowances)."""
    return (basic / WORKING_DAYS_PER_MONTH).quantize(TWO_PLACES)


# ─── PAYE on the payout (CFO directive 2026-08-17) ─────────────────────────────
#
# A leave cash-out paid to a SERVING employee is ordinary taxable employment
# income in the month it is paid — there is no concession and no special rate
# (unlike terminal gratuity/severance). So it is taxed at the employee's OWN
# marginal PAYE rate, computed off the SAME bracket table payroll uses:
#
#     tax = PAYE(monthly taxable pay + payout) − PAYE(monthly taxable pay)
#     net = payout − tax
#
# CFO chose this over a flat top rate (17-Aug-2026): a flat rate would badly
# over-tax junior staff, who would then have to reclaim from BURS.
#
# NOTE: this is the PAYE withholding on the cash-out. It is not a final
# assessment — the employee's annual return still trues everything up.

def tax_base_for(employee) -> tuple[Decimal, str]:
    """(monthly taxable pay in BWP, source period name) — the PAYE base the
    payout is stacked on top of. Read off the same payslip as the BASIC."""
    from payroll.paye import monthly_taxable_gross

    line = latest_basic_line_for(employee)
    if line is None:
        return Decimal('0.00'), ''
    return monthly_taxable_gross(line.payslip), line.payslip.period.period_name


def tax_and_net(tax_base: Decimal, payout: Decimal,
                brackets=None) -> tuple[Decimal, Decimal]:
    """(PAYE on the payout, net payable). Uses the ACTIVE TaxBracket rows — the
    rate is never hardcoded here, so a BURS change is a data change (e.g.
    `manage.py seed_burs_2026_2027`) and never a code change.

    Pass *brackets* to reuse an already-fetched schedule; callers in a loop MUST
    do so (`_tax_schedule` would otherwise re-query the same rows for every
    half-day step — up to 400 identical SELECTs per quote page load).

    If no brackets are seeded the PAYE engine returns 0 and net == gross. That is
    deliberately the SAME fail-open behaviour payroll has, so the two can never
    disagree. It is not a silent hole: an empty bracket table zeroes PAYE for the
    whole company's payroll at the same time, which the dual HR + Finance payroll
    sign-off catches loudly. (Fable 5 ruling 2026-08-17: fail-open stands. Do not
    "fix" it into a hard error — that would make an HRIS page's availability
    depend on tax-table state and diverge from the engine it must match.)
    """
    from payroll.paye import active_brackets_from_db, marginal_tax_on_extra

    if brackets is None:
        brackets = active_brackets_from_db()
    payout = Decimal(payout or 0)
    tax = marginal_tax_on_extra(tax_base, payout, brackets)
    if tax > payout:                 # can't withhold more than we're paying
        tax = payout
    return tax.quantize(TWO_PLACES), (payout - tax).quantize(TWO_PLACES)


def annual_available(profile) -> Decimal:
    """Current annual-leave days available from the single source of truth
    (hris.leave_balance — already net of in-flight/approved encashments)."""
    from .leave_balance import balances_for_profile
    for b in balances_for_profile(profile):
        if b['code'] == 'annual':
            return Decimal(str(b['available']))
    return Decimal('0')


def provision_rows(company_scoped_employees) -> dict:
    """The leave-pay provision register: one row per active employee — basic,
    daily rate, annual balance, provision = rate × balance."""
    rows = []
    total_provision = Decimal('0.00')
    total_days = Decimal('0.00')
    unvalued = 0

    employees = (company_scoped_employees
                 .filter(status='active')
                 .select_related('company', 'hris_profile')
                 .order_by('full_name'))
    for emp in employees:
        profile = getattr(emp, 'hris_profile', None)
        basic, basic_src = latest_basic_for(emp)
        rate = daily_rate_for(basic) if basic > 0 else Decimal('0.00')
        days = annual_available(profile)
        provision = (rate * days).quantize(TWO_PLACES)
        if basic <= 0:
            unvalued += 1
        total_provision += provision
        total_days += days
        rows.append({
            'employee_id':   str(emp.id),
            'employee_name': emp.full_name,
            'department':    emp.department or '',
            'company':       getattr(emp.company, 'code', '') or getattr(emp.company, 'name', '') or '',
            'basic_salary':  str(basic),
            'basic_source':  basic_src,
            'daily_rate':    str(rate),
            'balance_days':  str(days),
            'provision':     str(provision),
            'has_profile':   profile is not None,
        })
    return {
        'as_at': timezone.localdate().isoformat(),
        'rows': rows,
        'totals': {
            'employees':    len(rows),
            'unvalued':     unvalued,
            'balance_days': str(total_days),
            'provision':    str(total_provision.quantize(TWO_PLACES)),
        },
    }


# ─── Apply (self-service) ──────────────────────────────────────────────────────

def _parse_days(raw) -> Decimal:
    try:
        days = Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError, TypeError) as exc:
        raise ValidationError(f"Days {raw!r} is not a number.") from exc
    # NaN / Infinity parse fine as Decimal but blow up the comparisons below —
    # reject them explicitly (Fable 5 review 2026-07-21).
    if not days.is_finite():
        raise ValidationError("Days must be a real number.")
    if days <= 0:
        raise ValidationError("Days must be greater than zero.")
    if (days * 2) % 1 != 0:
        raise ValidationError("Days must be in half-day steps (e.g. 2 or 2.5).")
    return days


# Leave encashment pays out real cash, so the applicant must MOTIVATE it in their
# own words — at least 50 words — same bar as a staff loan (CFO 2026-07-22).
MIN_REASON_WORDS = 50

# CFO directive 2026-07-23: a staff member must keep a MINIMUM residual annual-
# leave balance after encashing — they cannot cash out to zero. Configurable via
# settings.LEAVE_ENCASH_MIN_RESIDUAL_DAYS so the CFO can change it without a
# deploy; defaults to 10 days.
MIN_RESIDUAL_DAYS = Decimal(str(getattr(settings, 'LEAVE_ENCASH_MIN_RESIDUAL_DAYS', 10)))


def _validate_reason(raw) -> str:
    reason = (raw or '').strip()
    n = len(reason.split())
    if n < MIN_REASON_WORDS:
        raise ValidationError(
            f"Please motivate your encashment in your own words — at least "
            f"{MIN_REASON_WORDS} words (you have {n}). Explain why you need to cash out leave.")
    return reason


def my_quote(user) -> dict:
    """What the applicant's self-service form shows: their own balance, daily
    rate and the max they can encash. Safe for any employee (own data only)."""
    emp = getattr(user, 'employee_record', None)
    if emp is None:
        return {'has_record': False}
    profile = getattr(emp, 'hris_profile', None)
    basic, src = latest_basic_for(emp)
    rate = daily_rate_for(basic) if basic > 0 else Decimal('0.00')
    available = annual_available(profile)
    max_encashable = max(Decimal('0'), available - MIN_RESIDUAL_DAYS)
    tax_base, _ = tax_base_for(emp)
    return {
        'has_record':    True,
        'employee_name': emp.full_name,
        'basic_salary':  str(basic),
        'basic_source':  src,
        'daily_rate':    str(rate),
        'available_days': str(available),
        'min_residual_days': str(MIN_RESIDUAL_DAYS),
        'max_encashable_days': str(max_encashable),
        'can_apply':     basic > 0 and max_encashable > 0,
        'tax_base':      str(tax_base),
        # PAYE is progressive, so tax is NOT a fixed percentage of the payout —
        # it can straddle a band boundary. Rather than re-implement the bracket
        # walk in the browser (two calculators = drift on a tax number), the
        # server pre-computes the exact tax for every legal day-count and the
        # form just looks it up. Half-day steps, capped at max_encashable, so
        # this is at most ~2 × the leave balance in entries.
        'tax_by_days':   _tax_schedule(rate, max_encashable, tax_base),
    }


# Hard ceiling on the pre-computed schedule. A leave balance far above this
# means bad data, not a real entitlement — the form still works, it just stops
# previewing tax past the cap (apply-time still computes the exact figure).
_MAX_SCHEDULE_DAYS = Decimal('200')


def _tax_schedule(daily_rate: Decimal, max_days: Decimal, tax_base: Decimal) -> dict:
    """{days-as-string: {payout, tax, net}} for every half-day up to max_days."""
    out: dict[str, dict] = {}
    if daily_rate <= 0 or max_days <= 0:
        return out
    # Fetch the bracket schedule ONCE — this loop runs up to 400 times and
    # tax_and_net would otherwise re-query the same rows on every step.
    from payroll.paye import active_brackets_from_db
    brackets = active_brackets_from_db()
    cap = min(max_days, _MAX_SCHEDULE_DAYS)
    step = Decimal('0.5')
    d = step
    while d <= cap:
        payout = (daily_rate * d).quantize(TWO_PLACES)
        tax, net = tax_and_net(tax_base, payout, brackets)
        out[_days_key(d)] = {'payout': str(payout), 'tax': str(tax), 'net': str(net)}
        d += step
    return out


def _days_key(d: Decimal) -> str:
    """'2' for whole days, '2.5' for halves — matches what the form sends."""
    return str(d.quantize(Decimal('1')) if d == d.to_integral_value() else d)


@transaction.atomic
def apply_encashment(*, applicant, days, reason: str = '') -> LeaveEncashment:
    """An employee applies to encash their OWN annual leave."""
    emp = getattr(applicant, 'employee_record', None)
    if emp is None:
        raise ValidationError(
            "No employee record is linked to your account — HR must link it "
            "before you can apply.")
    if emp.status != 'active':
        raise ValidationError("Only active employees can apply.")

    # An employee with no HR profile has no leave-balance row, so the balance
    # engine would return a phantom zero-usage figure and NOTHING would reserve
    # the days — unlimited repeat applications (Fable 5 review 2026-07-21).
    # Require the profile, same as we require the employee record.
    profile = getattr(emp, 'hris_profile', None)
    if profile is None:
        raise ValidationError(
            "Your HR profile isn't set up yet — ask HR to complete your record "
            "before you can apply.")

    # No hire date, no encashment (CFO 2026-09-07). Without it the balance
    # engine falls back to 1 January and hands a new joiner a full year-to-date
    # accrual: an August intern was valued at 14.00 days (21 / 12 x 8) and an
    # encashment was raised against every one of them. The balance shown is then
    # an assumption, and an assumption must not be turned into a payment. Blocked
    # here rather than silently recomputed, because loading the real start date
    # is an HR data fix and the difference is somebody's money.
    if emp.hire_date is None:
        raise ValidationError(
            "Your start date isn't recorded, so your leave balance can't be "
            "worked out yet. Ask HR to add it to your record, then apply.")

    days = _parse_days(days)
    reason = _validate_reason(reason)
    # Lock this profile's in-flight encashments so two concurrent applications
    # can't both pass the balance check (TOCTOU — Fable 5 review 2026-07-21).
    list(LeaveEncashment.objects.select_for_update().filter(
        profile=profile, status__in=LeaveEncashment.OPEN_STATUSES))
    available = annual_available(profile)
    if days > available:
        raise ValidationError(
            f"You have {available} annual-leave day(s) available — "
            f"cannot encash {days}.")
    # Must keep the minimum residual balance after encashing (CFO 2026-07-23).
    max_encashable = available - MIN_RESIDUAL_DAYS
    if days > max_encashable:
        raise ValidationError(
            f"You must keep at least {MIN_RESIDUAL_DAYS} annual-leave day(s) "
            f"after encashing. With {available} available you can encash at "
            f"most {max(Decimal('0'), max_encashable)}.")

    basic, basic_src = latest_basic_for(emp)
    if basic <= 0:
        raise ValidationError(
            "No basic salary is on your payslips yet — HR must load payroll "
            "before your encashment can be valued.")
    rate = daily_rate_for(basic)
    amount = (rate * days).quantize(TWO_PLACES)
    # PAYE snapshot — same discipline as the payout: server-computed at apply
    # time and frozen on the row, so a later BURS rate change or salary move
    # never silently reprices an in-flight or approved encashment.
    tax_base, _tax_src = tax_base_for(emp)
    tax_amount, net_amount = tax_and_net(tax_base, amount)

    # The first leg is the CFO's sign-off. When the applicant IS the CFO, that
    # leg has no independent approver (only a backup super-admin login), so the
    # request freezes if that backup is unavailable — exactly what bit the CFO's
    # own encashment on 2026-08-24. Skip the self-leg: the CFO's own request is
    # signed by the two independent legs below (HR then Finance). The applicant
    # stays fully barred from approving, and both remaining approvers are
    # independent of the applicant, so segregation of duties still holds.
    # (CFO directive 2026-08-24.)
    cfo_applicant = is_cfo(applicant)
    initial_status = (LeaveEncashment.Status.PENDING_HR if cfo_applicant
                      else LeaveEncashment.Status.PENDING_CFO)
    skip_note = (
        "[CFO leg skipped at apply — the applicant is the CFO and cannot "
        "approve their own request (segregation of duties). It is signed by the "
        "two independent legs, HR then Finance. CFO directive 2026-08-24.]"
        if cfo_applicant else '')

    enc = LeaveEncashment.objects.create(
        employee=emp,
        profile=profile,
        company=emp.company,
        days=days,
        basic_salary=basic,
        daily_rate=rate,
        amount=amount,
        tax_base=tax_base,
        tax_amount=tax_amount,
        net_amount=net_amount,
        balance_at_request=available,
        basic_source=basic_src,
        reason=reason,
        applicant=applicant,
        applicant_email=getattr(applicant, 'email', '') or '',
        status=initial_status,
        decision_notes=skip_note,
    )
    from .leave_encash_notify import notify_applied, notify_stage_advanced
    try:
        # CFO applicant → the CFO leg is skipped, so notify the NEXT approver
        # (HR), not the CFO. Everyone else → the CFO is the first approver.
        (notify_stage_advanced if cfo_applicant else notify_applied)(enc)
    except Exception:                      # noqa: BLE001 — never block on email
        log.exception("Encashment %s: apply notify failed", enc.pk)
    return enc


# ─── Leaver final leave pay (CFO 2026-09-05) ─────────────────────────────────

def settlement_quote(employee, last_day=None) -> dict:
    """What a leaver's final leave pay would be: FULL annual balance to the last
    working day (accrual stops there), BASIC ÷ 24, PAYE at their marginal rate.
    No residual floor — a leaver is paid everything. Read-only."""
    profile = getattr(employee, 'hris_profile', None)
    basic, src = latest_basic_for(employee)
    rate = daily_rate_for(basic) if basic > 0 else Decimal('0.00')
    days = Decimal('0')
    if profile is not None:
        from .leave_balance import balances_for_profile
        # Balance AS AT the last day: temporarily read the profile with the
        # termination date in place so accrual_cutoff stops there.
        emp = profile.employee
        saved = emp.termination_date
        try:
            if last_day is not None:
                emp.termination_date = last_day
            for b in balances_for_profile(profile):
                if b['code'] == 'annual':
                    days = Decimal(str(b['available'])).quantize(TWO_PLACES)
        finally:
            emp.termination_date = saved
    gross = (rate * days).quantize(TWO_PLACES)
    tax_base, _ = tax_base_for(employee)
    tax, net = tax_and_net(tax_base, gross)
    return {
        'employee_name': employee.full_name,
        'last_day':      last_day.isoformat() if last_day else None,
        'days':          str(days),
        'basic_salary':  str(basic),
        'basic_source':  src,
        'daily_rate':    str(rate),
        'amount':        str(gross),
        'tax_base':      str(tax_base),
        'tax_amount':    str(tax),
        'net_amount':    str(net),
        'can_value':     basic > 0,
        'has_profile':   profile is not None,
    }


def raise_leaver_settlement(*, initiator, employee, last_day, reason: str = '',
                            actor=None) -> LeaveEncashment:
    """HR raises a LEAVER's final leave pay. Differs from apply_encashment:
      * raised BY HR (the initiator) FOR the leaver — not self-service;
      * the leaver may already be terminated (that is the point);
      * the FULL balance to the last day is paid — no residual floor, no
        50-word motivation;
      * same valuation (BASIC ÷ 24, PAYE marginal) and the SAME approval chain
        CFO → HR → Finance, with the initiator barred from approving.
    The record is a LeaveEncashment of kind SETTLEMENT so every existing
    approval screen, dashboard stream and register shows it unchanged."""
    if initiator is None or not getattr(initiator, 'pk', None):
        raise ValidationError('A leaver settlement must be raised by a signed-in HR user.')
    profile = getattr(employee, 'hris_profile', None)
    if profile is None:
        raise ValidationError(
            f"{employee.full_name} has no HR profile, so there is no leave balance to settle.")
    if isinstance(last_day, str):
        try:
            last_day = _dt.date.fromisoformat(last_day)
        except ValueError as exc:
            raise ValidationError('Last working day must be YYYY-MM-DD.') from exc
    if last_day is None:
        last_day = employee.termination_date or timezone.localdate()

    list(LeaveEncashment.objects.select_for_update().filter(
        profile=profile, status__in=LeaveEncashment.OPEN_STATUSES))
    if LeaveEncashment.objects.filter(
            profile=profile, kind=LeaveEncashment.Kind.SETTLEMENT,
            status__in=LeaveEncashment.OPEN_STATUSES).exists():
        raise ValidationError(f'A final leave pay is already in progress for {employee.full_name}.')

    q = settlement_quote(employee, last_day)
    days = Decimal(q['days'])
    if days <= 0:
        raise ValidationError(f'{employee.full_name} has no annual leave to pay out.')
    basic = Decimal(q['basic_salary'])
    if basic <= 0:
        raise ValidationError(
            f"No basic salary is on {employee.full_name}'s payslips — the final leave pay "
            'cannot be valued until payroll is loaded.')

    cfo_initiator = is_cfo(initiator)
    enc = LeaveEncashment.objects.create(
        employee=employee,
        profile=profile,
        company=employee.company,
        kind=LeaveEncashment.Kind.SETTLEMENT,
        last_day=last_day,
        days=days,
        basic_salary=basic,
        daily_rate=Decimal(q['daily_rate']),
        amount=Decimal(q['amount']),
        tax_base=Decimal(q['tax_base']),
        tax_amount=Decimal(q['tax_amount']),
        net_amount=Decimal(q['net_amount']),
        balance_at_request=days,
        basic_source=q['basic_source'],
        reason=(reason or '').strip()[:2000],
        applicant=initiator,
        applicant_email=getattr(initiator, 'email', '') or '',
        status=(LeaveEncashment.Status.PENDING_HR if cfo_initiator
                else LeaveEncashment.Status.PENDING_CFO),
        decision_notes=(
            f'[Leaver final leave pay raised by HR ({getattr(initiator, "email", "")}): '
            f'full balance {days} day(s) to {last_day}. '
            + ('CFO leg skipped — the initiator is the CFO.]' if cfo_initiator else ']')),
    )
    from .leave_encash_notify import notify_applied, notify_stage_advanced

    def _notify():
        try:
            (notify_stage_advanced if cfo_initiator else notify_applied)(enc)
        except Exception:                  # noqa: BLE001 — never block on email
            log.exception("Settlement %s: notify failed", enc.pk)
    # Raised inside the re-hire transaction: the approver must only be asked
    # once the whole move has committed (runs immediately outside a transaction).
    transaction.on_commit(_notify)
    return enc


# ─── Approval chain ────────────────────────────────────────────────────────────

_STAGE = {
    LeaveEncashment.Status.PENDING_CFO: (
        'cfo', is_cfo, 'cfo_approver', 'cfo_approved_at',
        LeaveEncashment.Status.PENDING_HR,
        "Only the CFO approves at this stage."),
    LeaveEncashment.Status.PENDING_HR: (
        'hr', is_hr, 'hr_approver', 'hr_approved_at',
        LeaveEncashment.Status.PENDING_FINANCE,
        "Only HR approves at this stage."),
    LeaveEncashment.Status.PENDING_FINANCE: (
        'finance', is_finance_approver, 'finance_approver', 'finance_approved_at',
        LeaveEncashment.Status.APPROVED,
        "Only the Financial Controller or Finance Manager approves at this stage."),
}


def can_approve_now(enc: LeaveEncashment, user) -> bool:
    """True if `user` can approve `enc` at its CURRENT stage (used for UI +
    the dashboard queue counts)."""
    stage = _STAGE.get(enc.status)
    if stage is None:
        return False
    name, authorised, _, _, _, _ = stage
    # Applicant can never approve their own request.
    if enc.applicant_id and user.pk == enc.applicant_id:
        return False
    # Dead-end escalation: CFO may sign a stage whose normal approver is the
    # applicant (mirrors approve(), CFO directive 2026-08-04).
    applicant = getattr(enc, 'applicant', None)
    escalating = bool(applicant and authorised(applicant)) and \
        (not authorised(user)) and is_cfo(user)
    if not authorised(user) and not escalating:
        return False
    if _already_involved(enc, user) and not escalating:
        return False
    return True


def _already_involved(enc: LeaveEncashment, user) -> bool:
    """Applicant, or already signed an earlier leg → cannot sign now (SoD)."""
    uid = user.pk
    return uid in {enc.applicant_id, enc.cfo_approver_id,
                   enc.hr_approver_id, enc.finance_approver_id}


@transaction.atomic
def approve(enc: LeaveEncashment, user) -> LeaveEncashment:
    """Advance `enc` one stage. The stage is chosen by its current status; the
    caller must hold that stage's authority and pass segregation of duties."""
    # Lock the row so two approvers can't advance/double-sign the same leg
    # concurrently (Fable 5 review 2026-07-21).
    enc = LeaveEncashment.objects.select_for_update().get(pk=enc.pk)
    stage = _STAGE.get(enc.status)
    if stage is None:
        raise ValidationError(f"Request is {enc.get_status_display()} — "
                              f"not awaiting approval.")
    name, authorised, approver_field, at_field, next_status, deny_msg = stage

    # Self-approval is ALWAYS barred, no exceptions (SoD, absolute).
    if enc.applicant_id and user.pk == enc.applicant_id:
        raise ValidationError(
            "Segregation of duties: you cannot approve your own application.")

    # Dead-end escalation (CFO directive 2026-08-04): if the person who would
    # normally sign THIS stage is the applicant — e.g. the HR head applies for
    # their own leave pay, so the HR leg has no independent approver — the CFO
    # may sign this leg on their behalf, otherwise the request freezes forever
    # with nobody able to move it. This relaxes SoD ONLY for the conflicted
    # stage: the applicant stays fully excluded, the other legs keep their own
    # independent approvers, and the escalation is written to the audit note.
    applicant = getattr(enc, 'applicant', None)
    stage_conflicted = bool(applicant and authorised(applicant))
    escalating = stage_conflicted and (not authorised(user)) and is_cfo(user)

    if not authorised(user) and not escalating:
        raise ValidationError(deny_msg)
    if _already_involved(enc, user) and not escalating:
        raise ValidationError(
            "Segregation of duties: you already signed an earlier stage of "
            "this application.")

    # No balance re-check needed here: the days were RESERVED at apply time and
    # every other leave booking / encashment nets this row's reservation out of
    # 'available', so the days can't be double-spent while this one is in flight.

    now = timezone.now()
    setattr(enc, approver_field, user)
    setattr(enc, at_field, now)
    enc.status = next_status
    update_fields = [approver_field, at_field, 'status', 'updated_at']
    if escalating:
        note = (f"[{name.upper()} leg approved by the CFO — the applicant was "
                f"the normal {name} approver, so SoD escalated this leg.]")
        enc.decision_notes = (f"{enc.decision_notes}\n{note}".strip()
                              if enc.decision_notes else note)
        update_fields.append('decision_notes')
    enc.save(update_fields=update_fields, audit_user=user)

    from . import leave_encash_notify as notify
    try:
        if enc.status == LeaveEncashment.Status.APPROVED:
            notify.notify_approved(enc)
        else:
            notify.notify_stage_advanced(enc)
    except Exception:                      # noqa: BLE001
        log.exception("Encashment %s: stage-advance notify failed", enc.pk)
    return enc


@transaction.atomic
def reject(enc: LeaveEncashment, user, notes: str = '') -> LeaveEncashment:
    """Reject at the current stage. Any approver authorised for the current
    stage may reject (not the applicant)."""
    enc = LeaveEncashment.objects.select_for_update().get(pk=enc.pk)
    stage = _STAGE.get(enc.status)
    if stage is None:
        raise ValidationError(f"Request is {enc.get_status_display()} — "
                              f"cannot be rejected.")
    name, authorised, _, _, _, deny_msg = stage
    if not authorised(user):
        raise ValidationError(deny_msg)
    if enc.applicant_id and user.pk == enc.applicant_id:
        raise ValidationError(
            "Segregation of duties: you cannot decide your own application.")
    enc.status = LeaveEncashment.Status.REJECTED
    enc.rejected_by = user
    enc.rejected_at = timezone.now()
    enc.rejected_stage = name
    enc.decision_notes = (notes or '').strip()
    enc.save(update_fields=['status', 'rejected_by', 'rejected_at',
                            'rejected_stage', 'decision_notes', 'updated_at'],
             audit_user=user)
    from .leave_encash_notify import notify_rejected
    try:
        notify_rejected(enc)
    except Exception:                      # noqa: BLE001
        log.exception("Encashment %s: notify_rejected failed", enc.pk)
    return enc


@transaction.atomic
def mark_paid(enc: LeaveEncashment, user) -> LeaveEncashment:
    enc = LeaveEncashment.objects.select_for_update().get(pk=enc.pk)
    if enc.status != LeaveEncashment.Status.APPROVED:
        raise ValidationError("Only fully-approved encashments can be paid.")
    if enc.payroll_processed:
        raise ValidationError("Already marked paid.")
    if not can_pay(user):
        raise ValidationError(
            "Only Finance (FC/FM) can mark an encashment paid.")
    # Don't let the applicant pay their own, even if they're FC/FM (Fable 5
    # review 2026-07-21).
    if enc.applicant_id and user.pk == enc.applicant_id:
        raise ValidationError("You cannot mark your own encashment paid.")
    enc.payroll_processed = True
    enc.payroll_processed_by = user
    enc.payroll_processed_at = timezone.now()
    enc.status = LeaveEncashment.Status.PAID
    enc.save(update_fields=['payroll_processed', 'payroll_processed_by',
                            'payroll_processed_at', 'status', 'updated_at'],
             audit_user=user)
    return enc
