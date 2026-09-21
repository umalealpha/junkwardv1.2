"""
payroll/signoff_service.py — the rules behind payroll sign-off.

CFO directive 2026-07-28 — DUAL sign-off, CFO out of the routine loop:
a company's month is CLOSED (and payslips release) only when BOTH
  * an HR signer   — Unami or Dorothy, and
  * a Finance signer — Kago or Pako
have signed the SAME current figures. Either side may send it back. If payroll
changes after one side signs, that signature is dropped and both must sign the
new figures (TOCTOU / the same guard Fable 5 found in leave encashment).

The CFO / a superuser may back-stop EITHER side if a signer is away, but the
two legs must still be two different people (segregation of duties).

Kept out of the views so the tests exercise the rules directly.
"""

from __future__ import annotations

import os

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import UserProfile, get_user_profile

from .signoff_models import PayrollSignOff, live_payroll_totals

# The named routine signers. Task reminders go to exactly these people; other
# holders of the same title may still sign (below), but the daily chase targets
# these mailboxes. Env-overridable so HR can change them without a deploy.
HR_SIGNER_LOCAL_PARTS_DEFAULT  = ('ubutale', 'dikgopoleng')   # Unami, Dorothy
FIN_SIGNER_LOCAL_PARTS_DEFAULT = ('pkago', 'ktshutlhedi')     # Pako, Kago


def _allow(env_name, default) -> set[str]:
    env = (os.environ.get(env_name) or '').strip()
    if env:
        return {p.strip().lower() for p in env.split(',') if p.strip()}
    return {x.lower() for x in default}


def _hr_allow() -> set[str]:
    return _allow('OMNI_PAYROLL_HR_SIGNERS', HR_SIGNER_LOCAL_PARTS_DEFAULT)


def _fin_allow() -> set[str]:
    return _allow('OMNI_PAYROLL_FIN_SIGNERS', FIN_SIGNER_LOCAL_PARTS_DEFAULT)


def _local(user) -> str:
    return ((getattr(user, 'email', '') or '').split('@', 1)[0] or '').strip().lower()


def _title(user):
    prof = get_user_profile(user)
    if prof is None or not getattr(prof, 'is_active', True):
        return None
    return prof.title


def signoff_side(user) -> str | None:
    """Which leg this user may sign: 'hr', 'finance', 'both', or None.

    'both' = CFO / superuser back-stop (may fill either leg, but SoD below
    still forces the two legs to be two different people).
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    if getattr(user, 'is_superuser', False):
        return 'both'
    title = _title(user)
    if title == UserProfile.Title.CFO:
        return 'both'
    local = _local(user)
    # Primary: the named allowlists. Title is a robustness fallback so the
    # right department can still sign if a mailbox changes.
    hr = local in _hr_allow() or title == UserProfile.Title.HR_MANAGER
    fin = (local in _fin_allow()
           or title in (UserProfile.Title.FINANCE_MANAGER,
                        UserProfile.Title.FINANCIAL_CONTROLLER))
    if hr and not fin:
        return 'hr'
    if fin and not hr:
        return 'finance'
    if hr and fin:            # someone in both lists → let them pick a side
        return 'both'
    return None


def can_sign_hr(user) -> bool:
    return signoff_side(user) in ('hr', 'both')


def can_sign_finance(user) -> bool:
    return signoff_side(user) in ('finance', 'both')


def can_sign_any(user) -> bool:
    return signoff_side(user) is not None


def _signers_for(allow_set):
    from django.contrib.auth.models import User
    out = []
    for u in User.objects.filter(is_active=True).exclude(email=''):
        local = _local(u)
        if not local or '+' in local:      # skip plus-tagged service accounts
            continue
        if local in allow_set:
            out.append(u)
    return out


def payroll_hr_signers():
    """The HR people the daily chase reminds (Unami, Dorothy)."""
    return _signers_for(_hr_allow())


def payroll_finance_signers():
    """The Finance people the daily chase reminds (Pako, Kago)."""
    return _signers_for(_fin_allow())


def companies_awaiting_signoff(period):
    """[(company, row_or_None, needs_hr, needs_fin)] for entities that have a
    payroll this period but are not yet fully signed off at the current figures.
    A REJECTED row needs both legs again."""
    from core.models import Company

    rows = {r.company_id: r for r in PayrollSignOff.objects.filter(period=period)}
    out = []
    for company in Company.objects.all().order_by('name'):
        if live_payroll_totals(period, company)['headcount'] == 0:
            continue
        row = rows.get(company.id)
        if row is None:
            out.append((company, None, True, True))
            continue
        drifted = row.drift() is not None
        rejected = row.status == PayrollSignOff.Status.REJECTED
        # A drift or a rejection invalidates both legs.
        needs_hr  = rejected or drifted or not row.hr_signed_by_id
        needs_fin = rejected or drifted or not row.fin_signed_by_id
        if needs_hr or needs_fin:
            out.append((company, row, needs_hr, needs_fin))
    return out


def sign_payroll(period, company, user, side: str | None = None) -> PayrollSignOff:
    """Record ``user``'s HR or Finance signature on this company's month.

    Resolves the side from the user's role unless they are a back-stop
    (CFO/superuser), who must pass ``side``. When both legs are signed at the
    current figures the row flips to APPROVED and payslips release.
    """
    my = signoff_side(user)
    if my is None:
        raise ValidationError('You are not an HR or Finance payroll sign-off approver.')
    if my == 'both':
        if side not in ('hr', 'finance'):
            raise ValidationError('Choose which side you are signing — HR or Finance.')
    else:
        if side is not None and side != my:
            raise ValidationError(f'You can only sign the {my} side.')
        side = my

    totals = live_payroll_totals(period, company)
    if totals['headcount'] == 0:
        raise ValidationError(
            f'{company.name} has no payslips in {period.period_name} — '
            'calculate the payroll before signing it off.')

    row, created = PayrollSignOff.objects.get_or_create(
        period=period, company=company,
        defaults={
            'submitted_by': user, 'submitted_at': timezone.now(),
            'headcount': totals['headcount'], 'gross_total': totals['gross'],
            'paye_total': totals['paye'], 'net_total': totals['net'],
            'status': PayrollSignOff.Status.SUBMITTED,
        })

    # If the payroll moved since the stored figures, drop BOTH legs — the two
    # signatures must cover the same current numbers.
    if not created and (row.drift() is not None
                        or row.status == PayrollSignOff.Status.REJECTED):
        row.hr_signed_by = row.fin_signed_by = None
        row.hr_signed_at = row.fin_signed_at = None
        row.approved_by = None
        row.approved_at = None
        if row.submitted_by_id is None:
            row.submitted_by = user
            row.submitted_at = timezone.now()

    # Segregation of duties: the other leg must be a different person.
    other_id = row.fin_signed_by_id if side == 'hr' else row.hr_signed_by_id
    if other_id and other_id == user.id:
        raise ValidationError(
            'Segregation of duties: the HR and Finance signatures must be two '
            'different people.')

    now = timezone.now()
    if side == 'hr':
        row.hr_signed_by = user
        row.hr_signed_at = now
    else:
        row.fin_signed_by = user
        row.fin_signed_at = now

    # The figures on the row are always the current ones being signed.
    row.headcount   = totals['headcount']
    row.gross_total = totals['gross']
    row.paye_total  = totals['paye']
    row.net_total   = totals['net']
    row.rejected_by = None
    row.rejected_at = None
    row.rejection_reason = ''

    if row.hr_signed_by_id and row.fin_signed_by_id:
        row.status = PayrollSignOff.Status.APPROVED
        row.approved_by = user            # the second signature completes it
        row.approved_at = now
    else:
        row.status = PayrollSignOff.Status.SUBMITTED

    row.save()
    return row


def reject_signoff(row: PayrollSignOff, user, reason: str) -> PayrollSignOff:
    """Either an HR or a Finance signer can send a month back for a fix."""
    if signoff_side(user) is None:
        raise ValidationError('Payroll sign-off is restricted to HR / Finance signers.')
    if not (reason or '').strip():
        raise ValidationError('Say what needs fixing before sending it back.')
    row.status = PayrollSignOff.Status.REJECTED
    row.rejected_by = user
    row.rejected_at = timezone.now()
    row.rejection_reason = reason.strip()
    row.hr_signed_by = row.fin_signed_by = None
    row.hr_signed_at = row.fin_signed_at = None
    row.approved_by = None
    row.approved_at = None
    row.save()
    return row


def is_signed_off(period, company) -> bool:
    """True only when THIS company's month carries BOTH signatures and the
    payslips have not moved since. Gates staff payslip release."""
    if period is None or company is None:
        return False
    row = (PayrollSignOff.objects
           .filter(period=period, company=company,
                   status=PayrollSignOff.Status.APPROVED)
           .first())
    return bool(row and row.both_signed and row.drift() is None)


def pending_signoff_count() -> int:
    """Company-months still needing at least one signature (latest period)."""
    from .models import PayrollPeriod
    period = PayrollPeriod.objects.order_by('-start_date').first()
    if period is None:
        return 0
    return len(companies_awaiting_signoff(period))


# ── The teeth: staff cannot be issued a payslip for an unsigned payroll ──────
# Enforced from this month FORWARD only. Every period before the cutover has no
# sign-off row and never will — gating those would retroactively cut all staff
# off from their own payslip history. Override with settings.PAYROLL_SIGNOFF_FROM.
SIGNOFF_ENFORCED_FROM = '2026-07'


def _enforced_from() -> str:
    from django.conf import settings
    return getattr(settings, 'PAYROLL_SIGNOFF_FROM', SIGNOFF_ENFORCED_FROM)


def release_blocked_reason(payslip) -> str | None:
    """Why this payslip may not be issued to the employee yet — None if it may.

    Applies to the employee's OWN copy (self-service list + PDF) and to an
    HR/Finance-initiated send. Payroll staff keep full internal access to check
    a run; this only stops an unsigned payroll reaching staff.
    """
    period = getattr(payslip, 'period', None)
    if period is None:
        return None
    name = period.period_name or ''
    if name < _enforced_from():          # 'YYYY-MM' sorts correctly as text
        return None
    if is_signed_off(period, payslip.company):
        return None
    entity = payslip.company.name if payslip.company_id else 'this company'
    return (f'{entity} payroll for {name} has not been signed off by HR and '
            'Finance yet, so payslips have not been released.')
