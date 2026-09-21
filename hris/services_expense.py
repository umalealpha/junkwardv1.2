"""
hris/services_expense.py

CFO directive 2026-05-24 — Payroll + HR upgrades pass, item #4.

`approve_expense_claim(claim, user)` is the bridge between an HRIS
ExpenseClaim and either accounts payable (billing.Invoice — a DRAFT
vendor_bill) or the next-open PayrollPeriod (a PayslipLine against
component_code='REIMBURSEMENT').

Bible-check guard
-----------------
hris/ + payroll/ writes only. We DO call billing.Invoice.objects.create()
to materialise a draft vendor bill — that's an explicit allowed contract
(the bill stays DRAFT, approval + posting routes through the existing
billing workflow). No imports from reporting/ or ledger/.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')

REIMBURSEMENT_CODE = 'REIMBURSEMENT'


# ---------------------------------------------------------------------------
# Internal helpers — contact resolution + payslip materialisation
# ---------------------------------------------------------------------------

def _resolve_employee_contact(profile):
    """Best-effort match of UserProfile → billing.Contact (type=employee).

    Looks up by:
      1. profile.user.employee_record (the OneToOne on payroll.Employee)
         then by employee.external_ref → Contact.external_ref.
      2. Fall back to a name/email match against active employee contacts.
    Returns None if no match — caller must create a placeholder contact
    upstream and re-call.
    """
    from billing.models import Contact

    user = getattr(profile, 'user', None)
    employee = getattr(user, 'employee_record', None) if user else None

    if employee:
        if employee.external_ref:
            c = Contact.objects.filter(
                contact_type=Contact.ContactType.EMPLOYEE,
                external_ref=employee.external_ref,
                is_active=True,
            ).first()
            if c:
                return c
        if employee.email:
            c = Contact.objects.filter(
                contact_type=Contact.ContactType.EMPLOYEE,
                email__iexact=employee.email,
                is_active=True,
            ).first()
            if c:
                return c
        c = Contact.objects.filter(
            contact_type=Contact.ContactType.EMPLOYEE,
            name__iexact=employee.full_name,
            is_active=True,
        ).first()
        if c:
            return c
    return None


def _next_open_period():
    """The next PayrollPeriod with status=OPEN, soonest start_date first."""
    from payroll.models import PayrollPeriod
    return (
        PayrollPeriod.objects
        .filter(status=PayrollPeriod.Status.OPEN)
        .order_by('start_date')
        .first()
    )


def _ensure_reimbursement_component():
    from payroll.models import PayslipComponent
    comp, _ = PayslipComponent.objects.get_or_create(
        code=REIMBURSEMENT_CODE,
        defaults={
            'name':       'Reimbursement',
            'kind':       PayslipComponent.Kind.EARNING_NON_TAXABLE,
            'sort_order': 25,
            'is_active':  True,
            'is_taxable': False,
        },
    )
    return comp


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@transaction.atomic
def approve_expense_claim(claim, user):
    """Mark `claim` approved and create the downstream artefact.

    Returns the created artefact (Invoice for AP, PayslipLine for payroll).
    Raises ValidationError when the bridge can't be built (e.g. no open
    payroll period, no contact link).
    """
    from .expense_claim_models import ExpenseClaim

    if claim.status == ExpenseClaim.Status.APPROVED:
        raise ValidationError('Expense claim is already approved.')
    if claim.amount is None or Decimal(claim.amount) <= 0:
        raise ValidationError({'amount': 'Amount must be > 0.'})

    method = claim.reimbursement_method

    artefact = None
    if method == ExpenseClaim.ReimbursementMethod.AP:
        artefact = _materialise_ap_bill(claim, user)
    elif method == ExpenseClaim.ReimbursementMethod.PAYROLL:
        artefact = _materialise_payroll_line(claim)
    else:
        raise ValidationError({'reimbursement_method': f'Unknown method: {method}'})

    claim.status = ExpenseClaim.Status.APPROVED
    claim.approved_by = user
    claim.approved_at = timezone.now()
    claim.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])

    logger.info(
        'ExpenseClaim %s approved via %s → %s',
        claim.id, method, artefact,
    )
    return artefact


# ---------------------------------------------------------------------------
# Branch: AP — create a DRAFT vendor bill
# ---------------------------------------------------------------------------

def _materialise_ap_bill(claim, user):
    from billing.models import Contact, Invoice

    contact = _resolve_employee_contact(claim.profile)
    if not contact:
        raise ValidationError({
            'profile':
            'No billing.Contact linked for this employee — create one '
            '(contact_type=employee) and re-approve.',
        })

    invoice = Invoice.objects.create(
        invoice_type=Invoice.InvoiceType.VENDOR_BILL,
        contact=contact,
        issue_date=claim.expense_date,
        currency_code_id=claim.currency or 'BWP',
        subtotal=claim.amount,
        total_amount=claim.amount,
        balance_due=claim.amount,
        status=Invoice.Status.DRAFT,
        source_type='hris_expense_claim',
        source_id=str(claim.id),
        description=(claim.description or claim.category or 'Employee expense claim')[:1000],
        created_by=user,
    )
    return invoice


# ---------------------------------------------------------------------------
# Branch: PAYROLL — add line to next-open period's payslip
# ---------------------------------------------------------------------------

def _materialise_payroll_line(claim):
    from payroll.models import Payslip, PayslipLine

    period = _next_open_period()
    if not period:
        raise ValidationError({
            '__all__':
            'No OPEN payroll period exists. Open the next month before '
            'approving payroll reimbursements.',
        })

    user = getattr(claim.profile, 'user', None)
    employee = getattr(user, 'employee_record', None) if user else None
    if not employee:
        raise ValidationError({
            'profile':
            'No payroll.Employee linked for this UserProfile. Link the '
            'profile.user to an Employee.user before approving.',
        })

    component = _ensure_reimbursement_component()
    payslip, _ = Payslip.objects.get_or_create(
        employee=employee, period=period,
        defaults={'company': employee.company_id and employee.company},
    )
    line, created = PayslipLine.objects.get_or_create(
        payslip=payslip, component=component,
        defaults={
            'amount': claim.amount,
            'notes':  f'Reimbursement: ExpenseClaim {claim.id}',
        },
    )
    if not created:
        line.amount = (Decimal(line.amount or ZERO) + Decimal(claim.amount))
        line.save(update_fields=['amount', 'updated_at'])
    return line
