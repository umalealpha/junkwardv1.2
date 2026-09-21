"""staff_loans/payment_request.py — raise the loan disbursement as a payment.

CFO, 15 September 2026, moving loan disbursement off HR:

    "the staff loan can come. I approve or Mr Arun approves, then it goes to
     finance. The finance manager or financial controller, Pako, can approve it
     and the payment is automatically loaded. Then a notification is sent to
     Human Resources for their records."

"Automatically loaded" means loaded INTO OMNI'S PAYMENT QUEUE, never paid.
Omni moves no money: the CFO still authorises in Omni and again in FNB with his
own phone and two-factor, and there is no second code path to the bank.

BE PRECISE ABOUT WHAT THIS DOES AND DOES NOT PASS THROUGH. It is created
directly at PENDING_CFO, because the CFO's instruction is ONE Finance approval
then his — the person releasing the loan IS the stage-one approver, and routing
it to PENDING_FINANCE would make Finance sign the same thing twice. The cost is
that it does not pass the checks a hand-typed payment does: the changed-bank
warning (PAY-BANK-01), the first-payee confirmation (PAY-BANK-03), the
duplicate committee route (PAY-DUP-01). That is defensible here because the
payee account is not typed by anyone — it comes from the employee's
HR-maintained payroll record — but it is a DECISION, not a side effect, and the
CFO has been told.

Modelled on taskboard/premium_refund_requests.raise_premium_refund_request, and
it inherits that module's hard-won lesson: a request raised with task=None sits
at PENDING_CFO for ever because every pay control hangs off the linked task.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from taskboard.models import OmniTask, PaymentRequest

log = logging.getLogger(__name__)


class StaffLoanPaymentRequestError(Exception):
    """Raised when the disbursement cannot be turned into a payment request."""


def raise_staff_loan_payment_request(app, entered_by, *, pay_from='') -> PaymentRequest:
    """Create the PENDING_CFO payment request that pays a disbursed staff loan.

    `app` is the StaffLoanApplication, already signed and being disbursed by
    Finance. `pay_from` names the bank account the issuance journal entry
    already credited, so whoever loads FNB pays from the same account the
    ledger says the money left. Returns the PaymentRequest.
    """
    from taskboard.payment_views import (_cfo_user, _next_ref, _render_html,
                                         _render_plaintext, _task_title)

    cfo = _cfo_user()
    if cfo is None:
        raise StaffLoanPaymentRequestError(
            'No CFO / approver account is configured, so the loan payment cannot '
            'be sent for authorisation. Nothing was raised.')

    emp = app.employee
    company = emp.company
    entity = getattr(company, 'code', '') or 'ADIC'
    amount = Decimal(app.effective_amount).quantize(Decimal('0.01'))
    if amount <= 0:
        raise StaffLoanPaymentRequestError(
            'The loan resolves to a zero amount — nothing was raised.')

    payee = (emp.full_name or '').strip() or f'Employee {emp.employee_number}'
    pay_from = (pay_from or '').strip() or 'the account on the loan record'
    try:
        loan_label = app.get_loan_type_display()
    except Exception:                       # noqa: BLE001
        loan_label = 'Staff loan'

    # Same key shape _render_html indexes directly; different keys raise
    # KeyError at render rather than at validation.
    line = {
        'description': f'{loan_label} disbursement — {payee} ({emp.employee_number})',
        'gl_code': '',
        'ref': str(emp.employee_number or ''),
        'amount': amount,
    }

    # The employee's own bank details — this money goes OUT to them, so the
    # account has to be on the request FNB is keyed from. bank_account_no is
    # encrypted at rest; it is read here, written to the request Finance and
    # the CFO already see, and NEVER put in a log line or an email.
    pr_data = {
        'entity': entity,
        'category': PaymentRequest.Category.STAFF_LOAN,
        'currency': 'BWP',
        'subject': f'Staff loan disbursement — {payee}',
        'payee': payee[:191],
        'line_items': [line],
        'total': amount,
        'opening_balance': Decimal('0.00'),
        'inputter': entered_by.get_full_name() or entered_by.username,
        'verifier': '',
        'account_name': (getattr(emp, 'full_name', '') or '')[:120],
        'account_number': (getattr(emp, 'bank_account_no', '') or ''),
        'bank_name': (getattr(emp, 'bank_name', '') or '')[:120],
        'summary': (
            f'{loan_label} of BWP {amount} for {payee} ({emp.employee_number}). '
            f'Approved by the CFO, signed by the employee, and released by '
            f'Finance on {timezone.localtime().strftime("%d %b %Y")}. The '
            f'accounting entry and the payroll deduction are already posted — '
            f'this request pays the employee. Pay from: {pay_from}. '
            f'Awaiting CFO authorisation.'),
    }

    for _attempt in range(6):
        pr_data['ref'] = _next_ref(entity)
        html = _render_html(pr_data)
        save_data = {**pr_data,
                     'line_items': [{**ln, 'amount': str(ln['amount'])}
                                    for ln in pr_data['line_items']]}
        try:
            with transaction.atomic():
                task = OmniTask.objects.create(
                    assigner=entered_by, assignee=cfo,
                    title=_task_title(entity, PaymentRequest.Category.STAFF_LOAN,
                                      'BWP', amount),
                    body=(pr_data['summary'] + '\n\n'
                          + _render_plaintext(pr_data))[:5000],
                    priority=OmniTask.Priority.HIGH,
                    status=OmniTask.Status.PENDING,
                    due_at=timezone.now() + datetime.timedelta(days=1),
                    source='payment_request',
                )
                return PaymentRequest.objects.create(
                    created_by=entered_by,
                    status=PaymentRequest.Status.PENDING_CFO,
                    formatted_html=html,
                    task=task,
                    decision_notes='Raised automatically when Finance released the staff loan.',
                    **save_data)
        except IntegrityError:
            # A duplicate reference number — take the next one. Nothing else in
            # this payload is unique, so there is no second collision to tell
            # apart the way the premium-refund raiser has to.
            continue
    raise StaffLoanPaymentRequestError(
        f'could not allocate a unique payment-request number for the loan to '
        f'{emp.employee_number} after 6 attempts — nothing was raised.')
