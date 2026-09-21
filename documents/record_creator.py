"""
documents/record_creator.py

Turns a confirmed DocumentUpload into a real Finance record (Invoice / Payment).

Design:
- One creator per supported action_type
- Records are created in DRAFT — never auto-posted, so a misclassified document
  doesn't silently land in the GL. The user reviews and posts/confirms in the
  dedicated module.
- Returns (model_label, instance) or (None, None) if no record applies.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from typing import Tuple, Optional

from django.contrib.auth.models import User

from billing.models import Contact, Invoice, InvoiceLine
from core.models import Company, Currency, TaxRate
from ledger.models import Account
from payments.models import Payment
from django.utils import timezone


ZERO = Decimal('0.00')


def _resolve_currency_code(extracted_data, details) -> str:
    """
    Determine currency from edits / AI extraction. Falls back to BWP.
    Validates against the Currency table so unknown codes don't crash later.
    """
    code = (
        (details or {}).get('currency')
        or (extracted_data or {}).get('currency')
        or 'BWP'
    )
    code = str(code).strip().upper() or 'BWP'
    if not Currency.objects.filter(code=code).exists():
        return 'BWP'
    return code


def _to_decimal(value, default=ZERO) -> Decimal:
    """Convert a value to Decimal, falling back if invalid/missing."""
    if value is None or value == '':
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _to_date(value) -> Optional[datetime.date]:
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _account(code: str) -> Optional[Account]:
    return Account.objects.filter(code=code, is_active=True).first()


def _tax_rate(*codes: str) -> Optional[TaxRate]:
    for code in codes:
        rate = TaxRate.objects.filter(tax_code=code, is_active=True).first()
        if rate:
            return rate
    return None


def _resolve_contact(name: str, contact_type: str) -> Contact:
    """
    Find a Contact by case-insensitive name match, or create a stub.
    contact_type: 'customer' | 'vendor' | 'broker' | etc.
    """
    name = (name or '').strip() or 'Unknown'
    contact = Contact.objects.filter(name__iexact=name).first()
    if contact:
        return contact
    return Contact.objects.create(
        name=name,
        contact_type=contact_type,
        currency_code_id='BWP',
        payment_terms_days=30,
        is_resident=True,
        wht_exempt=(contact_type == 'customer'),
    )


def _default_bank_account() -> Account:
    """1110 = FNB BWP operating (per CLAUDE.md)."""
    acct = _account('1110')
    if not acct:
        # Fallback: any active GL account flagged as bank
        acct = Account.objects.filter(is_bank_account=True, is_active=True).first()
    if not acct:
        raise RuntimeError(
            "No bank account in chart of accounts. "
            "Run setup_chart_of_accounts first."
        )
    return acct


# ---------------------------------------------------------------------------
# Invoice creators
# ---------------------------------------------------------------------------

def _create_invoice(
    upload, user: User, *,
    invoice_type: str,
    contact_type: str,
    default_revenue_or_expense_code: str,
    description_prefix: str,
) -> Tuple[str, Invoice]:
    data = (upload.extracted_data or {})
    details = (upload.suggested_action or {}).get('details', {}) or {}

    name = (
        details.get('vendor_name')
        or data.get('vendor_name')
        or data.get('possible_vendor')
        or data.get('customer_name')
        or 'Unknown'
    )
    contact = _resolve_contact(name, contact_type)

    issue_date = (
        _to_date(details.get('issue_date'))
        or _to_date(data.get('document_date'))
        or timezone.localdate()
    )

    amount = _to_decimal(details.get('total_amount') or data.get('total_amount'))
    if amount <= ZERO:
        amount = Decimal('0.01')  # placeholder for draft; user edits before posting

    line_account = (
        _account(str(details.get('suggested_account') or '').strip())
        or _account(default_revenue_or_expense_code)
    )
    if line_account is None:
        raise RuntimeError(
            f"Account {default_revenue_or_expense_code} not found. "
            "Run setup_chart_of_accounts first."
        )

    tax_code = _tax_rate('VAT_ZERO', 'VAT_EXEMPT')
    if tax_code is None:
        raise RuntimeError("No VAT_ZERO/VAT_EXEMPT tax rate. Run setup_initial_data first.")

    ref = (
        details.get('reference_number') or data.get('reference_number')
        or (data.get('references') or [None])[0]
    )
    desc_extra = f" (ref {ref})" if ref else ""

    currency_code = _resolve_currency_code(data, details)

    invoice = Invoice(
        invoice_type=invoice_type,
        contact=contact,
        company=Company.get_default(),
        currency_code_id=currency_code,
        issue_date=issue_date,
        description=f"{description_prefix} from {upload.original_filename}{desc_extra}",
        source_type='document_upload',
        source_id=str(upload.id),
        created_by=user,
    )
    invoice.save(audit_user=user)

    InvoiceLine.objects.create(
        invoice=invoice,
        account=line_account,
        description=(details.get('vendor_name') or name)[:500],
        quantity=Decimal('1'),
        unit_price=amount,
        tax_code=tax_code,
    )
    invoice.recalculate_totals()
    invoice.save(audit_user=user)

    return 'invoice', invoice


def create_customer_invoice(upload, user):
    return _create_invoice(
        upload, user,
        invoice_type=Invoice.InvoiceType.CUSTOMER_INVOICE,
        contact_type=Contact.ContactType.CUSTOMER,
        default_revenue_or_expense_code='4100',
        description_prefix='Customer invoice',
    )


def create_vendor_bill(upload, user):
    return _create_invoice(
        upload, user,
        invoice_type=Invoice.InvoiceType.VENDOR_BILL,
        contact_type=Contact.ContactType.VENDOR,
        default_revenue_or_expense_code='5100',
        description_prefix='Vendor bill',
    )


def create_credit_note(upload, user):
    return _create_invoice(
        upload, user,
        invoice_type=Invoice.InvoiceType.CREDIT_NOTE,
        contact_type=Contact.ContactType.CUSTOMER,
        default_revenue_or_expense_code='4100',
        description_prefix='Credit note',
    )


# ---------------------------------------------------------------------------
# Payment creators
# ---------------------------------------------------------------------------

def _create_payment(
    upload, user: User, *,
    payment_type: str,
    contact_type: str,
) -> Tuple[str, Payment]:
    data = (upload.extracted_data or {})
    details = (upload.suggested_action or {}).get('details', {}) or {}

    name = (
        details.get('vendor_name')
        or data.get('vendor_name')
        or data.get('customer_name')
        or data.get('possible_vendor')
        or 'Unknown'
    )
    contact = _resolve_contact(name, contact_type)

    pay_date = (
        _to_date(details.get('payment_date'))
        or _to_date(data.get('document_date'))
        or timezone.localdate()
    )

    amount = _to_decimal(
        details.get('amount')
        or details.get('total_amount')
        or data.get('total_amount')
    )
    if amount <= ZERO:
        amount = Decimal('0.01')

    ref = str(
        details.get('reference')
        or details.get('reference_number')
        or (data.get('references') or [None])[0]
        or upload.original_filename
    )[:200]

    currency_code = _resolve_currency_code(data, details)

    payment = Payment(
        payment_type=payment_type,
        contact=contact,
        company=Company.get_default(),
        bank_account=_default_bank_account(),
        payment_date=pay_date,
        currency_code_id=currency_code,
        amount=amount,
        payment_method=Payment.PaymentMethod.BANK_TRANSFER,
        reference=ref,
        description=f"From upload: {upload.original_filename}",
        created_by=user,
    )
    payment.save(audit_user=user)
    return 'payment', payment


def record_payment_received(upload, user):
    return _create_payment(
        upload, user,
        payment_type=Payment.PaymentType.RECEIVED,
        contact_type=Contact.ContactType.CUSTOMER,
    )


def record_payment_made(upload, user):
    return _create_payment(
        upload, user,
        payment_type=Payment.PaymentType.SENT,
        contact_type=Contact.ContactType.VENDOR,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

CREATORS = {
    'create_customer_invoice':  create_customer_invoice,
    'create_vendor_bill':       create_vendor_bill,
    'create_credit_note':       create_credit_note,
    'record_payment_received':  record_payment_received,
    'record_payment_made':      record_payment_made,
    # 'import_bank_statement' and 'manual_review' deliberately not handled:
    # bank statements need a CSV/format mapping; manual_review is by definition
    # an opt-out from auto-creation.
}


def create_record_for(upload, user) -> Tuple[Optional[str], Optional[object]]:
    """
    Dispatch to the right creator based on the upload's action_type.
    Returns (model_label, instance) or (None, None) if no creator applies.
    Raises any underlying creation errors.
    """
    action = (upload.suggested_action or {}).get('action_type')
    creator = CREATORS.get(action)
    if not creator:
        return None, None
    return creator(upload, user)
