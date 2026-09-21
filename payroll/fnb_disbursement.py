"""
payroll/fnb_disbursement.py

Load an APPROVED payroll period's net pay into FNB as a single bulk
CustomerCreditTransferInitiation (ISO 20022 / pain.001) — the same proven
endpoint (`/paymentExecution/initiate/v1`) that FNB has accepted live.

DESIGN (CFO directive 2026-07-24):
  - omni only *loads* the batch into FNB. The CFO still approves it MANUALLY
    inside FNB — that manual FNB approval is the money gate. omni never
    releases funds on its own.
  - ADIC ONLY. The debit (source) account is hard-scoped to the Alpha Direct
    (ADIC) FNB current account; payslips of any other company are excluded.
  - Reuses the audited two-phase money-safety submitter in
    `fnb.payments.submit_eft_batch` (PENDING record committed before the POST,
    POST outside any transaction, timeout/5xx → UNKNOWN never auto-retried).
    We feed it per-payslip "once-off payee" adapters so we never have to
    persist ~150 vendor Payment rows or post a second GL leg (payroll already
    posts the aggregate salary JE at POSTED).

MASTER SWITCH: nothing is sent to FNB unless settings.PAYROLL_FNB_LOAD_ENABLED
is True. Preview (dry_run) always works. Deploy with the switch OFF; flip it
on only after a small live test.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from fnb.destination_bank import branch_code_problem

from .bank_codes import account_is_mangled
from .models import PayrollPeriod, Payslip

log = logging.getLogger(__name__)

ZERO = Decimal('0.00')

# ADIC scoping — overridable via settings, but defaults are the verified
# production values (checked against live FNBSyncLog + banking data 2026-07-24).
ADIC_COMPANY_CODE = getattr(settings, 'PAYROLL_FNB_COMPANY_CODE', 'ADIC')
ADIC_SOURCE_ACCOUNT_NUMBER = getattr(
    settings, 'PAYROLL_FNB_SOURCE_ACCOUNT_NUMBER', '62403392335',
)


# ---------------------------------------------------------------------------
# Per-payslip adapter — quacks like a once-off payments.Payment so we can reuse
# fnb.payments.build_batch_payload / submit_eft_batch unchanged.
# ---------------------------------------------------------------------------
@dataclass
class _PayslipPayee:
    payment_number: str
    payee_name: str
    payee_account_number: str
    payee_branch_code: str
    amount: Decimal
    amount_bwp: Decimal
    description: str
    payee_bank_name: str = ''
    currency_code_id: str = 'BWP'
    is_once_off: bool = True
    vendor_bank_account: None = None
    reference: str = ''
    remittance_email: None = None
    pk: None = None


def _resolve_adic_company():
    from core.models import Company
    try:
        return Company.objects.get(code=ADIC_COMPANY_CODE)
    except Company.DoesNotExist as exc:
        raise ValidationError(
            f'ADIC company (code={ADIC_COMPANY_CODE!r}) not found. Payroll '
            'FNB load is hard-scoped to Alpha Direct and cannot run without it.'
        ) from exc


def _resolve_source_account(adic_company):
    """The ADIC FNB current account to debit. Verified: 62403392335
    (FNBB Cheque A/C, owner_company=ADIC)."""
    from banking.models import BankAccount
    acct = (BankAccount.objects
            .filter(account_number=ADIC_SOURCE_ACCOUNT_NUMBER, is_active=True)
            .first())
    if acct is None:
        raise ValidationError(
            f'ADIC FNB source account {ADIC_SOURCE_ACCOUNT_NUMBER} not found '
            '(or inactive) in banking. Cannot load salaries.'
        )
    owner = getattr(getattr(acct, 'gl_account', None), 'owner_company_id', None)
    if owner and adic_company and owner != adic_company.pk:
        raise ValidationError(
            'Refusing to load: source account '
            f'{ADIC_SOURCE_ACCOUNT_NUMBER} is not owned by ADIC '
            f'(owner_company={owner}). ADIC-only guard.'
        )
    return acct


@dataclass
class PayrollFNBPreview:
    period_name: str
    included: list = field(default_factory=list)   # dicts: name, acct(masked), amount
    excluded: list = field(default_factory=list)   # dicts: name, reason
    total: Decimal = ZERO
    count: int = 0


def build_period_preview(
    period: PayrollPeriod,
    *,
    employee_ids: Optional[list] = None,
) -> PayrollFNBPreview:
    """Validate + summarise what WOULD be sent. No side effects, no POST.

    Includes only ADIC, APPROVED payslips with net > 0 and usable bank
    details (account number present + not Excel-mangled + branch code
    present). Everything else is listed under `excluded` with a reason so
    the CFO sees exactly who is not being paid and why.
    """
    adic = _resolve_adic_company()
    qs = (period.payslips
          .filter(status=Payslip.Status.APPROVED, company=adic)
          .select_related('employee', 'company'))
    if employee_ids:
        qs = qs.filter(employee_id__in=employee_ids)

    pre = PayrollFNBPreview(period_name=period.period_name)
    for ps in qs:
        emp = ps.employee
        name = (emp.full_name or '').strip() or f'Employee {emp.employee_number}'
        acct = (emp.bank_account_no or '').strip()
        branch = (emp.bank_branch_code or '').strip()
        net = ps.net_amount or ZERO

        if net <= ZERO:
            pre.excluded.append({'name': name, 'reason': f'net pay is {net}'})
            continue
        if not acct:
            pre.excluded.append({'name': name, 'reason': 'no bank account number'})
            continue
        if account_is_mangled(acct):
            pre.excluded.append({'name': name,
                                 'reason': 'bank account number looks corrupted (re-import as text)'})
            continue
        if not branch:
            pre.excluded.append({'name': name, 'reason': 'no bank branch code'})
            continue
        # 🔴 EXCLUDE THE ONE PERSON, NEVER REFUSE THE RUN. Payroll builds ONE
        # batch for every employee, and build_batch_payload now RAISES on a
        # malformed creditor branch code — so a single '64967' (the Excel
        # leading-zero trap this file already warns about) would stop nobody's
        # salary loading, not just theirs. The same rule, applied per line,
        # here at the caller. (Fable 5.1, 17-Sep-2026, checklist L66.)
        _branch_problem = branch_code_problem(emp.bank_name or '', branch)
        if _branch_problem:
            pre.excluded.append({'name': name,
                                 'reason': f'branch code: {_branch_problem}'})
            continue

        pre.included.append({
            'name': name,
            'account_masked': ('*' * max(0, len(acct) - 4)) + acct[-4:],
            'branch': branch,
            'amount': net,
        })
        pre.total += net

    pre.count = len(pre.included)
    return pre


def _payees_from_preview(period, adic, employee_ids):
    """Build the adapter objects for the INCLUDED payslips (same filter as
    the preview) — used only on the real submit path."""
    qs = (period.payslips
          .filter(status=Payslip.Status.APPROVED, company=adic)
          .select_related('employee'))
    if employee_ids:
        qs = qs.filter(employee_id__in=employee_ids)

    payees = []
    for ps in qs:
        emp = ps.employee
        acct = (emp.bank_account_no or '').strip()
        branch = (emp.bank_branch_code or '').strip()
        net = ps.net_amount or ZERO
        # Must mirror the preview's filter EXACTLY, including the branch-code
        # shape check — a payee the preview excluded must not reach the batch.
        if (net <= ZERO or not acct or account_is_mangled(acct) or not branch
                or branch_code_problem(emp.bank_name or '', branch)):
            continue
        # endToEndId ≤ 35 chars (ISO 20022): SAL-<period>-<empno>
        pay_no = f'SAL-{period.period_name}-{emp.employee_number}'[:35]
        payees.append(_PayslipPayee(
            payment_number=pay_no,
            payee_name=(emp.full_name or '').strip() or f'Employee {emp.employee_number}',
            payee_account_number=acct,
            payee_branch_code=branch,
            payee_bank_name=(emp.bank_name or '').strip(),
            amount=net,
            amount_bwp=net,
            description=f'Salary {period.period_name}',
        ))
    return payees


def load_period_to_fnb(
    period: PayrollPeriod,
    user: User,
    *,
    employee_ids: Optional[list] = None,
    dry_run: bool = True,
):
    """Load this period's ADIC net pay into FNB.

    dry_run=True (default): returns a PayrollFNBPreview only — SAFE, no POST.
    dry_run=False: actually POSTs the bulk to FNB via the audited
        submit_eft_batch, links the resulting FNBBatchSubmission to this
        period, and returns it. Gated by settings.PAYROLL_FNB_LOAD_ENABLED.

    `employee_ids` restricts the run to a subset — used for the initial
    small (2-3 staff) live test before the full run.
    """
    if period.status not in (PayrollPeriod.Status.APPROVED,
                             PayrollPeriod.Status.POSTED):
        raise ValidationError(
            f'Payroll period {period.period_name} is {period.get_status_display()}. '
            'It must be APPROVED before salaries can be loaded to FNB.'
        )

    preview = build_period_preview(period, employee_ids=employee_ids)
    if dry_run:
        return preview

    if not getattr(settings, 'PAYROLL_FNB_LOAD_ENABLED', False):
        raise ValidationError(
            'Payroll→FNB load is switched OFF (PAYROLL_FNB_LOAD_ENABLED). '
            'Preview is available; sending is disabled until it is enabled.'
        )
    if preview.count == 0:
        raise ValidationError(
            'Nothing to load — no ADIC payslip has both usable bank details '
            'and net pay > 0. See the preview for who was excluded and why.'
        )

    # Idempotency at the period level — never load the same period twice
    # while a batch is still live.
    from fnb.models import FNBBatchSubmission
    live = (FNBBatchSubmission.objects
            .filter(payroll_period=period)
            .exclude(status__in=[FNBBatchSubmission.Status.FAILED,
                                 FNBBatchSubmission.Status.CANCELLED])
            .exists())
    if live and not employee_ids:
        raise ValidationError(
            f'Period {period.period_name} already has a live FNB batch. '
            'Refusing to load it again (double-pay guard). Cancel/verify the '
            'existing batch in FNB first.'
        )

    adic = _resolve_adic_company()
    source_account = _resolve_source_account(adic)
    payees = _payees_from_preview(period, adic, employee_ids)

    from fnb.payments import submit_eft_batch
    batch = submit_eft_batch(
        payees,
        source_account=source_account,
        user=user,
        # Payroll's payees are transient objects built from payslips, so
        # they carry no creator and the second-person check has nothing
        # to compare. Declared here so the exemption is visible.
        # REVISIT before the payroll money leg goes live: the right
        # control is releaser != the person who approved the payroll
        # PERIOD, which is a real recorded identity.
        allow_single_person=True,
        service_level_code=getattr(settings, 'PAYROLL_FNB_SERVICE_LEVEL', 'SDVA'),
    )
    # Link the batch to the period (audit + the double-pay guard above).
    from fnb.models import FNBBatchSubmission
    FNBBatchSubmission.objects.filter(pk=batch.pk).update(payroll_period=period)
    log.info('Payroll %s loaded to FNB: batch=%s count=%d total=%s',
             period.period_name, batch.idempotency_key, preview.count, preview.total)
    batch.refresh_from_db()
    return batch
