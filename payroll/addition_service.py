"""
payroll/addition_service.py — rules behind "Add Employee to Payroll".

Rules kept out of the views so the tests exercise them directly (mirrors
signoff_service). Maker-checker with segregation of duties; the approver is the
existing Finance sign-off leg (signoff_service.can_sign_finance — Finance
Manager / Financial Controller / CFO back-stop); a signed-off period is frozen;
PAYE + totals reuse Payslip.recompute_totals() and the live BURS brackets.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .addition_models import PayrollAdditionRequest
from .models import Employee, Payslip, PayslipComponent, PayslipLine, PayrollPeriod
from .signoff_service import can_sign_finance, is_signed_off

ZERO = Decimal('0.00')
CENTS = Decimal('0.01')


def _name_key(name: str) -> str:
    """Normalised match key — collapse internal whitespace + casefold.

    The July-2026 duplicate-row bug came from name variants slipping the match
    ('Bonang  Lentswe' double-space, case differences). Collapse + casefold so
    an obvious re-add is caught before a second blank-shell Employee is minted.
    """
    return re.sub(r'\s+', ' ', (name or '').strip()).casefold()


def _to_dec(v) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, '') else 0)).quantize(CENTS)
    except Exception:  # noqa: BLE001
        return ZERO


def possible_matches(full_name, employee_number, company):
    """Existing (non-terminated) employees this addition might duplicate —
    matched on employee_number OR normalised name, company-first ordering."""
    key = _name_key(full_name)
    number = (employee_number or '').strip()
    hits, seen = [], set()
    if number:
        for e in Employee.objects.filter(employee_number=number):
            if e.pk not in seen:
                hits.append(e); seen.add(e.pk)
    for e in Employee.objects.exclude(status=Employee.Status.TERMINATED):
        if _name_key(e.full_name) == key and e.pk not in seen:
            hits.append(e); seen.add(e.pk)
    # company match first so approval links within the same entity
    hits.sort(key=lambda e: 0 if e.company_id == getattr(company, 'id', None) else 1)
    return hits


def _guard_period(period, company):
    if period is None or company is None:
        raise ValidationError('Period and entity are required.')
    if period.status != PayrollPeriod.Status.OPEN:
        raise ValidationError(
            f'Period {period.period_name} is {period.get_status_display().lower()} — '
            'you cannot add an employee to it.')
    if is_signed_off(period, company):
        raise ValidationError(
            f'{company.name} {period.period_name} is already signed off — '
            'it must be reopened before an employee can be added.')


@transaction.atomic
def create_addition(*, period, company, user, full_name, employee_number='',
                    department='', basic=ZERO, commission=ZERO, incentive=ZERO,
                    allowances=None):
    """Stage a new employee for the period in PENDING status. Returns
    (request, warnings). Nothing hits the payroll until it is approved."""
    full_name = (full_name or '').strip()
    if not full_name:
        raise ValidationError('Full name is required.')
    basic = _to_dec(basic)
    if basic <= ZERO:
        raise ValidationError('Basic salary is required and must be greater than zero.')
    _guard_period(period, company)

    clean_allow = []
    for a in (allowances or []):
        code = (a.get('code') or '').strip()
        amt = _to_dec(a.get('amount'))
        if code and amt != ZERO:
            clean_allow.append({'code': code, 'amount': str(amt)})

    req = PayrollAdditionRequest(
        period=period, company=company, full_name=full_name,
        employee_number=(employee_number or '').strip(),
        department=(department or '').strip(),
        basic=basic, commission=_to_dec(commission), incentive=_to_dec(incentive),
        allowances=clean_allow, requested_by=user,
        status=PayrollAdditionRequest.Status.PENDING,
    )
    req.save(audit_user=user,
             audit_description=f'Requested add {full_name} to {company.code} {period.period_name}')

    warnings = []
    dupes = possible_matches(full_name, req.employee_number, company)
    if dupes:
        warnings.append(
            'An employee with this name or number already exists: '
            + ', '.join(f'{e.full_name} ({e.employee_number or "no number"})' for e in dupes[:5])
            + '. On approval this will LINK to the existing record, not create a duplicate.')
    dup_pending = (PayrollAdditionRequest.objects
                   .filter(period=period, company=company,
                           status=PayrollAdditionRequest.Status.PENDING)
                   .exclude(pk=req.pk))
    if any(_name_key(r.full_name) == _name_key(full_name) for r in dup_pending):
        warnings.append('Another pending request for this name already exists for this period.')
    return req, warnings


@transaction.atomic
def approve_addition(req, user):
    """Finance approves: create/link the employee, create a DRAFT payslip with
    the entered components, compute PAYE from the BURS brackets. Only now does
    it affect headcount and the period totals."""
    if req.status != PayrollAdditionRequest.Status.PENDING:
        raise ValidationError(f'This request is already {req.get_status_display().lower()}.')
    if not can_sign_finance(user):
        raise PermissionDenied('Only a Finance sign-off (Finance Manager / Financial '
                               'Controller / CFO) can approve payroll additions.')
    if req.requested_by_id and req.requested_by_id == getattr(user, 'id', None):
        raise ValidationError('You cannot approve your own submission (segregation of duties).')

    period, company = req.period, req.company
    _guard_period(period, company)

    # Resolve or create the employee — reuse an existing match, never mint a
    # duplicate blank-shell row (July-2026 bug).
    matches = possible_matches(req.full_name, req.employee_number, company)
    employee = matches[0] if matches else None
    linked = employee is not None
    if employee is None:
        base_no = (req.employee_number.strip()
                   or f"{company.code}-{re.sub(r'[^A-Za-z0-9]+', '', req.full_name)[:12].upper()}")
        empno = base_no or f"EMP-{uuid.uuid4().hex[:6].upper()}"
        while Employee.objects.filter(employee_number=empno).exists():
            empno = f"{base_no}-{uuid.uuid4().hex[:4].upper()}"
        employee = Employee.objects.create(
            full_name=req.full_name, department=req.department,
            company=company, employee_number=empno,
            status=Employee.Status.ACTIVE,
        )

    if Payslip.objects.filter(employee=employee, period=period).exists():
        raise ValidationError(
            f'{employee.full_name} already has a payslip in {period.period_name}.')

    payslip = Payslip.objects.create(
        employee=employee, period=period, company=company,
        status=Payslip.Status.DRAFT,
    )
    payslip.save(audit_user=user, audit_description=f'Added via payroll addition {req.pk}')

    comps = {c.code: c for c in PayslipComponent.objects.filter(is_active=True)}

    def _add_line(code, amount):
        amount = _to_dec(amount)
        comp = comps.get((code or '').strip())
        if comp is None or amount == ZERO:
            return
        PayslipLine.objects.create(payslip=payslip, component=comp, amount=amount)

    _add_line('BASIC', req.basic)
    _add_line('COMMISSION', req.commission)
    _add_line('INCENTIVE', req.incentive)
    for a in (req.allowances or []):
        _add_line(a.get('code'), a.get('amount'))

    # Reuse the payslip engine: gross from earnings, PAYE from live BURS
    # brackets, net = gross − PAYE − deductions (a fresh add has no deductions).
    payslip.recompute_totals()
    _rp = getattr(payslip, '_recomputed_paye', None)
    if _rp is not None:
        payslip.overwrite_paye_line(_rp, user=user)
        payslip.recompute_totals(recompute_paye=False)
    payslip.save(audit_user=user,
                 audit_description='Payroll addition approved — draft payslip created')

    req.status = PayrollAdditionRequest.Status.APPROVED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.created_employee = employee
    req.created_payslip = payslip
    req.linked_existing = linked
    req.save(audit_user=user,
             audit_description=(f'Approved add {req.full_name} '
                                f'({"linked existing employee" if linked else "new employee"})'))
    return req


@transaction.atomic
def reject_addition(req, user, comment):
    """Finance rejects with a mandatory comment; nothing posts to the payroll."""
    if req.status != PayrollAdditionRequest.Status.PENDING:
        raise ValidationError(f'This request is already {req.get_status_display().lower()}.')
    if not can_sign_finance(user):
        raise PermissionDenied('Only a Finance sign-off can reject payroll additions.')
    comment = (comment or '').strip()
    if not comment:
        raise ValidationError('A rejection comment is required.')
    req.status = PayrollAdditionRequest.Status.REJECTED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.rejection_comment = comment
    req.save(audit_user=user, audit_description=f'Rejected add {req.full_name}: {comment[:80]}')
    return req


def pending_for_approver(user):
    """PENDING additions this Finance approver may action (SoD: excludes their
    own submissions). Empty queryset for non-approvers."""
    if not can_sign_finance(user):
        return PayrollAdditionRequest.objects.none()
    qs = PayrollAdditionRequest.objects.filter(status=PayrollAdditionRequest.Status.PENDING)
    uid = getattr(user, 'id', None)
    if uid:
        qs = qs.exclude(requested_by_id=uid)
    return qs
