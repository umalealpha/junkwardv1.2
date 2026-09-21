"""claims_automation/veritas.py — the salvage side of an Agreement of Loss
(CFO 19-Sep-2026).

When we settle a write-off we take the wreck, and Veritas (Motor Liquidators,
company VCM) takes it from us. We invoice Veritas **20% of what we actually paid
the client** — the net settlement on the authorised Agreement of Loss.

    This REPLACES the 20%-of-sum-insured basis written in the earlier CR-006
    spec. The CFO changed it on 19-Sep-2026; Finance (Kago) is being told.

Two controls the CFO asked for, both enforced here and in processor.approve():
  * The Agreement of Loss cannot be authorised until Veritas confirms they hold
    the vehicle, the blue book, the spare keys and the plates. A claims manager
    may override in writing; the override is recorded and shown.
  * The charge is raised only when BOTH are true (authorised AND in possession),
    so we never invoice for a wreck the yard does not hold.

Omni never posts it. The invoice is created as a DRAFT for Finance to check and
post, and nothing at all is created until Kago names the income account
(CLAIMS_VERITAS_SALVAGE_INCOME_ACCOUNT); until then the charge waits, visibly.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ClaimLetter, SalvageHandover, VeritasSalvageCharge

log = logging.getLogger(__name__)

TWO = Decimal('0.01')
# Prod already carries 'Veritas Capital Management Pty Ltd' as a SUPPLIER on every
# entity. We bill them as a customer, so we match on the stem and re-use an existing
# customer row if one exists; only then do we create one, in prod's own spelling,
# so we never make a near-duplicate debtor (Fable review, 20-Sep-2026).
VERITAS_NAME_STEM = 'veritas capital management'
VERITAS_CONTACT_NAME = 'Veritas Capital Management Pty Ltd'


def income_account_code() -> str:
    return str(getattr(settings, 'CLAIMS_VERITAS_SALVAGE_INCOME_ACCOUNT', '') or '').strip()


def receivable_account_code() -> str:
    return str(getattr(settings, 'CLAIMS_VERITAS_SALVAGE_RECEIVABLE_ACCOUNT', '260001') or '').strip()


def _money(value) -> Decimal:
    return Decimal(str(value or '0').replace(',', '')).quantize(TWO, rounding=ROUND_HALF_UP)


def charge_amount(settlement) -> Decimal:
    """20% of the settlement, HALF UP, never negative."""
    net = _money(settlement)
    if net < 0:
        net = Decimal('0.00')
    return (net * VeritasSalvageCharge.RATE).quantize(TWO, rounding=ROUND_HALF_UP)


def possession_ok(case) -> tuple[bool, str]:
    """(may the Agreement of Loss be authorised, why not)."""
    handover = getattr(case, 'handover', None)
    if handover is None:
        return False, ('Veritas has not confirmed they hold the vehicle. Ask the yard to '
                       'complete the handover checklist, or override with a reason.')
    if handover.overridden:
        return True, ''
    if handover.complete:
        return True, ''
    return False, 'The yard has not confirmed: ' + ', '.join(handover.missing or ['the checklist'])


def record_override(case, user, reason: str) -> SalvageHandover:
    handover, _ = SalvageHandover.objects.get_or_create(case=case)
    handover.override_by, handover.override_at = user, timezone.now()
    handover.override_reason = (reason or '').strip()[:2000]
    handover.save()
    log.warning('claims_automation: AoL possession overridden on %s by %s',
                case.claim_ref, getattr(user, 'username', '?'))
    return handover


def _standard_vat():
    from core.models import TaxRate
    return (TaxRate.objects.filter(tax_code='VAT_STD', is_active=True).first()
            or TaxRate.objects.filter(rate=Decimal('14.00'), is_active=True)
                              .order_by('tax_code').first())


def _veritas_contact(adic_company):
    """The Veritas customer record on ADIC's books. Related party (IAS 24)."""
    from billing.models import Contact
    fields = {f.name for f in Contact._meta.get_fields()}
    matches = Contact.objects.filter(name__icontains=VERITAS_NAME_STEM)
    if 'company' in fields and adic_company is not None:
        matches = matches.filter(company=adic_company)
    customer = matches.filter(contact_type=Contact.ContactType.CUSTOMER).first()
    if customer:
        return customer
    data = {'name': VERITAS_CONTACT_NAME, 'contact_type': Contact.ContactType.CUSTOMER}
    if 'company' in fields:
        data['company'] = adic_company
    if 'is_related_party' in fields:
        data['is_related_party'] = True
    if 'related_party_relationship' in fields:
        data['related_party_relationship'] = 'Group company — salvage yard (Motor Liquidators / VCM)'
    return Contact.objects.create(**data)


def draft_invoice(charge: VeritasSalvageCharge, user=None) -> bool:
    """Create the DRAFT customer invoice for Finance. Never posts it.
    Returns True when a draft now exists."""
    if charge.invoice_id:
        return True
    code = income_account_code()
    if not code:
        return False
    from billing.models import Contact, Invoice, InvoiceLine  # noqa: F401
    from core.models import Company
    from ledger.models import Account

    income = Account.objects.filter(code=code).first()
    receivable = Account.objects.filter(code=receivable_account_code()).first()
    if income is None or receivable is None:
        charge.note = (f'Account not found in the chart of accounts '
                       f'(income {code}, receivable {receivable_account_code()}).')[:2000]
        charge.save(update_fields=['note', 'updated_at'])
        return False

    adic = Company.objects.filter(code='ADIC').first()
    contact = _veritas_contact(adic)
    vehicle = (charge.case.facts or {}).get('vehicle') or {}
    label = ' '.join(str(v) for v in (vehicle.get('make'), vehicle.get('model'),
                                      vehicle.get('registration')) if v)
    with transaction.atomic():
        invoice = Invoice.objects.create(
            invoice_type=Invoice.InvoiceType.CUSTOMER_INVOICE,
            contact=contact, company=adic, issue_date=timezone.localdate(),
            receivable_account=receivable, created_by=user,
        )
        # VAT: charged at the standard rate, i.e. the 20% is treated as the
        # VAT-EXCLUSIVE value of the wreck. Finance confirms the treatment before
        # posting the draft (asked of Kago, 19-Sep-2026).
        InvoiceLine.objects.create(
            invoice=invoice, account=income, tax_code=_standard_vat(),
            description=(f'Salvage — claim {charge.case.claim_ref}'
                         + (f' — {label}' if label else '')
                         + f' — 20% of settlement {charge.settlement:,.2f}')[:255],
            quantity=1, unit_price=charge.amount,
        )
        invoice.recalculate_totals()
        invoice.save()
        charge.invoice = invoice
        charge.status = VeritasSalvageCharge.Status.DRAFTED
        charge.note = 'Draft invoice raised. Finance checks the VAT treatment and posts it.'
        charge.save(update_fields=['invoice', 'status', 'note', 'updated_at'])
    return True


def maybe_raise(case, user=None) -> str:
    """Called after an Agreement of Loss is authorised AND after the yard confirms
    possession. Raises the charge once, whichever happens last. Plain-words result."""
    letter = case.letters.filter(kind=ClaimLetter.Kind.AOL, status__in=(
        ClaimLetter.Status.APPROVED, ClaimLetter.Status.SENT)).first()
    if letter is None:
        return ''
    ok, _why = possession_ok(case)
    if not ok:
        return ''
    if VeritasSalvageCharge.objects.filter(letter=letter).exists():
        return ''
    settlement = _money(((letter.figures or {}).get('net')) or 0)
    charge = VeritasSalvageCharge.objects.create(
        case=case, letter=letter, settlement=settlement,
        amount=charge_amount(settlement),
        status=VeritasSalvageCharge.Status.PENDING_CONFIG,
    )
    if draft_invoice(charge, user):
        return (f'Veritas invoice drafted for {charge.amount:,.2f} '
                f'(20% of {settlement:,.2f}) — Finance to check and post')
    charge.note = ('Waiting for Finance to name the salvage income account '
                   '(CLAIMS_VERITAS_SALVAGE_INCOME_ACCOUNT). Nothing is posted.')
    charge.save(update_fields=['note', 'updated_at'])
    return (f'Veritas owes {charge.amount:,.2f} (20% of {settlement:,.2f}) — held until '
            f'Finance names the income account')


def raise_pending(user=None) -> int:
    """Once Kago names the account, turn every waiting charge into a draft invoice."""
    done = 0
    for charge in VeritasSalvageCharge.objects.filter(
            status=VeritasSalvageCharge.Status.PENDING_CONFIG, invoice__isnull=True):
        if draft_invoice(charge, user):
            done += 1
    return done
