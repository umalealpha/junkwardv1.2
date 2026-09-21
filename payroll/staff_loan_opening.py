"""
payroll/staff_loan_opening.py — the Financial Controller enters each staff
member's loan balance as at a chosen month, and Omni deducts it from then on
(CFO 2026-08-28).

Deliberately simple + safe:
  * Pako (FC) types the NET balance (already includes the flat interest) and the
    monthly deduction. Human-entered, human-owned — no automated migration off
    the messy legacy deduction lines.
  * It creates an ordinary `EmployeeLoan` at rate 0 (interest is already baked
    into the net balance, so the engine must not add more) with
    term = ceil(balance / monthly). The EXISTING monthly engine
    (loan_service.apply_loan_repayments) then deducts a LOAN_REPAYMENT line,
    decrements the balance and closes the loan at zero — nothing new to run.
  * One active loan per employee: if a person already has an active balance we
    SKIP and report, never stack a second loan (double-deduction guard).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Any

from django.db import transaction

ZERO = Decimal('0.00')


def _term_months(balance: Decimal, monthly: Decimal) -> int:
    if monthly <= 0:
        return 0
    return int((balance / monthly).to_integral_value(rounding=ROUND_CEILING))


def create_opening_balances(*, rows: list[dict], start_period, user=None) -> dict[str, Any]:
    """Create staff-loan accounts from Pako's entered opening balances.

    rows: [{employee_id, net_balance, monthly_deduction}]. Returns created +
    skipped (with reasons). Idempotent per employee: an existing active loan is
    never doubled."""
    from payroll.contract_models import EmployeeLoan
    from payroll.models import Employee

    # Bind the LOAN_REPAYMENT component's GL account BEFORE any opening loan is
    # created (Prompt 01, Shared Contract v1, 2026-09-12): an opening-balance
    # loan has no prior disbursement, so if this is the first staff loan ever
    # touched, the component was left unbound and posting the period later
    # failed with "missing posting_account_code". Uses the same shared helper
    # loan_service.apply_loan_repayments() calls — one binding, never two.
    from payroll.loan_service import bind_loan_repayment_account
    bind_loan_repayment_account()

    created, skipped = [], []
    with transaction.atomic():
        for r in rows:
            emp = None
            eid = str(r.get('employee_id') or '').strip()
            enum = str(r.get('employee_number') or '').strip()
            base = (Employee.objects.select_for_update()
                    .filter(is_archived=False)
                    .exclude(status=Employee.Status.TERMINATED))
            if eid:
                emp = base.filter(pk=eid).first()
            elif enum:
                emp = base.filter(employee_number__iexact=enum).first()
            if emp is None:
                skipped.append({'ref': eid or enum, 'reason': 'employee not found'})
                continue
            try:
                bal = Decimal(str(r.get('net_balance'))).quantize(Decimal('0.01'))
                monthly = Decimal(str(r.get('monthly_deduction'))).quantize(Decimal('0.01'))
            except Exception:  # noqa: BLE001
                skipped.append({'employee': emp.full_name, 'reason': 'balance/monthly not a number'})
                continue
            if bal <= 0 or monthly <= 0:
                skipped.append({'employee': emp.full_name, 'reason': 'balance and monthly must be greater than zero'})
                continue
            if monthly > bal:
                monthly = bal   # last-and-only instalment
            if EmployeeLoan.objects.filter(employee=emp,
                                           status=EmployeeLoan.Status.ACTIVE,
                                           outstanding__gt=0).exists():
                skipped.append({'employee': emp.full_name,
                                'reason': 'already has an active loan balance — clear it first'})
                continue
            term = _term_months(bal, monthly)
            loan = EmployeeLoan.objects.create(
                employee=emp, principal=bal, outstanding=bal,
                annual_rate_pct=ZERO,          # flat interest already in the net balance
                term_months=term, start_period=start_period,
                status=EmployeeLoan.Status.ACTIVE,
            )
            if hasattr(loan, 'save'):
                try:
                    loan.save(audit_user=user,
                              audit_description=f'Opening staff-loan balance entered by FC: '
                                                f'{emp.full_name} P{bal} over {term} month(s)')
                except TypeError:
                    pass
            created.append({'employee': emp.full_name, 'balance': str(bal),
                            'monthly': str(monthly), 'months': term, 'loan_id': str(loan.pk)})
    return {'created': created, 'created_count': len(created),
            'skipped': skipped, 'skipped_count': len(skipped),
            'start_period': start_period.period_name}
