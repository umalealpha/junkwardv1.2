"""bonu/payment_bridge.py — turn a confirmed lawyer bill into a payment loaded to FNB.

The money leg reuses the ERP's existing rails end to end. Nothing here posts a
journal or moves money on its own:

  * the payee is the firm's VAULTED bank account (procurement.VendorBankAccount —
    maker-checker, immutable once active), reached via LawFirm.vendor_contact, and
    resolved LIVE at payment time so a rotated/retired account can't go stale;
  * the Payment goes through the standard maker step (can_originate_controlled_txn)
    and, being outbound, the dual-control quorum (1× Finance + 1× CFO/CEO, no
    self-approval) — all enforced by payments.models, untouched here;
  * loading to FNB is the existing CFO-gated POST /fnb/submit-batch/, and the money
    only leaves when the CFO releases it inside the FNB app with his phone.
    Omni NEVER moves money.
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


class BridgeError(Exception):
    """A plain-message setup problem the caller must surface as a 400, never a 500."""


def _bonu_ref(invoice) -> str:
    return f'BONU {invoice.invoice_number}'[:200]


def firm_active_bank(firm):
    """The firm's LIVE (ACTIVE) vaulted bank account, or None."""
    if not firm.vendor_contact_id:
        return None
    from procurement.models import VendorBankAccount
    return (VendorBankAccount.objects
            .filter(contact_id=firm.vendor_contact_id, status=VendorBankAccount.Status.ACTIVE)
            .order_by('-is_default', '-created_at').first())


def _paying_company(firm):
    from core.models import Company
    if firm.vendor_contact_id and firm.vendor_contact.company_id:
        return firm.vendor_contact.company
    return Company.objects.filter(code='ADIC').first()


def raise_payment_for_invoice(invoice, *, bank_account_id, payment_date, user):
    """Create a SENT Payment for an APPROVED lawyer bill and submit it for approval.

    Returns the Payment. Raises BridgeError (→ 400) on any setup problem so a caller
    never turns a missing vault link into a 500.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction

    from bonu.models import BonuInvoice
    from payments.models import Payment, resolve_paying_bank

    firm = invoice.firm
    # Only a bill the office has APPROVED for payment may become a payment. A
    # received / under-review / QUERIED / REJECTED bill must never be raiseable —
    # the Fee Queries hold and the paid-twice history both depend on this gate.
    if invoice.status != BonuInvoice.Status.APPROVED:
        raise BridgeError(
            f'Only an APPROVED bill can be paid. {invoice.invoice_number} is '
            f'"{invoice.get_status_display()}".')
    if not firm.vendor_contact_id:
        raise BridgeError(
            f'{firm.name} is not linked to a vendor record yet. Add the firm to the '
            f'vendor vault and link it before a payment can be raised.')
    vba = firm_active_bank(firm)
    if vba is None:
        raise BridgeError(
            f'{firm.name} has no APPROVED bank account in the vault. Add and approve '
            f'its bank details first — a firm is never paid on unverified details.')

    try:
        amount = Decimal(str(invoice.total or 0))
    except (InvalidOperation, TypeError):
        raise BridgeError('This bill has no readable amount.')
    if amount <= 0:
        raise BridgeError('This bill has no positive amount to pay.')

    # Dedupe on the SERVER-derived reference only — never a caller-supplied one, or
    # a custom reference would slip a second payment for the same bill past this.
    ref = _bonu_ref(invoice)
    if Payment.objects.filter(contact_id=firm.vendor_contact_id, reference=ref).exists():
        raise BridgeError(f'A payment for {invoice.invoice_number} is already in the queue.')

    bank_gl = resolve_paying_bank(bank_account_id)
    if bank_gl is None:
        raise BridgeError('Choose the bank account the payment goes out from.')

    try:
        with transaction.atomic():
            payment = Payment(
                payment_type=Payment.PaymentType.SENT,
                contact=firm.vendor_contact, company=_paying_company(firm), bank_account=bank_gl,
                vendor_bank_account=vba,
                payment_date=payment_date or timezone.localdate(),
                currency_code_id='BWP', amount=amount,
                payment_method=Payment.PaymentMethod.BANK_TRANSFER,
                reference=ref,
                description=f'BONU legal bill {invoice.invoice_number} — {firm.name}'[:1000],
                created_by=user,
            )
            payment.save(audit_user=user)
            payment.submit_for_approval(user)
    except ValidationError as e:
        # e.g. an amount policy cap — a clean 400, and no orphan DRAFT left behind.
        msgs = getattr(e, 'messages', None)
        raise BridgeError('; '.join(msgs) if msgs else str(e))
    return payment


# ─────────────────────────────────────────────────────────────── views ──────

def _deny_finance(request):
    """BONU financial gate — this is a money action, not intake."""
    from bonu.views import _deny
    return _deny(request)


def _require_maker(request):
    from core.models import get_user_profile
    prof = get_user_profile(request.user)
    if not (prof and prof.can_originate_controlled_txn):
        return Response(
            {'detail': 'Raising a payment needs a maker title (Financial Controller / '
                       'Senior Accountant / Accountant). A different person approves it.'},
            status=status.HTTP_403_FORBIDDEN)
    return None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def bonu_payables(request):
    """GET /api/v1/bonu/payables/ — confirmed lawyer bills and where each stands on payment."""
    denied = _deny_finance(request)
    if denied is not None:
        return denied
    from bonu.models import BonuInvoice
    from payments.models import Payment
    from procurement.models import VendorBankAccount

    invoices = list(BonuInvoice.objects.select_related('firm').order_by('-invoice_date')[:300])
    contact_ids = {i.firm.vendor_contact_id for i in invoices if i.firm.vendor_contact_id}
    # One query for which contacts have a LIVE vault account, one for existing
    # payments (keyed on the server-derived ref) — not two queries per row (K5).
    active_contacts = (set(VendorBankAccount.objects
                       .filter(contact_id__in=contact_ids, status=VendorBankAccount.Status.ACTIVE)
                       .values_list('contact_id', flat=True)) if contact_ids else set())
    pay_by_key = {}
    if contact_ids:
        refs = [_bonu_ref(i) for i in invoices]
        for p in Payment.objects.filter(reference__in=refs, contact_id__in=contact_ids):
            pay_by_key[(p.contact_id, p.reference)] = p

    rows = []
    for i in invoices:
        firm = i.firm
        ref = _bonu_ref(i)
        paid = pay_by_key.get((firm.vendor_contact_id, ref)) if firm.vendor_contact_id else None
        rows.append({
            'id': str(i.pk), 'invoice_number': i.invoice_number, 'firm': firm.name,
            'firm_id': str(firm.pk),
            'invoice_date': i.invoice_date.isoformat() if i.invoice_date else '',
            'total': float(i.total or 0), 'status': i.status,
            'linked': bool(firm.vendor_contact_id),
            'bank_ready': firm.vendor_contact_id in active_contacts,
            'payment': ({'id': str(paid.pk), 'reference': paid.reference, 'status': paid.status,
                         'approval_status': getattr(paid, 'approval_status', ''),
                         'bank_submitted': bool(getattr(paid, 'bank_submitted_at', None))}
                        if paid else None),
        })
    return Response({'invoices': rows,
                     'note': 'A bill can be paid once its firm is linked to the vault and its '
                             'bank details are approved. Money still leaves only in the FNB app.'})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def raise_bonu_payment(request, invoice_id):
    """POST /api/v1/bonu/invoices/<id>/raise-payment/ — raise a payment for one bill."""
    denied = _deny_finance(request) or _require_maker(request)
    if denied is not None:
        return denied
    from bonu.models import BonuInvoice

    inv = BonuInvoice.objects.select_related('firm').filter(pk=invoice_id).first()
    if inv is None:
        return Response({'detail': 'That bill is not on file.'}, status=status.HTTP_404_NOT_FOUND)

    data = request.data or {}
    pd = (data.get('payment_date') or '').strip()
    try:
        payment_date = datetime.date.fromisoformat(pd[:10]) if pd else None
    except ValueError:
        return Response({'detail': 'payment_date is not a valid date.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        p = raise_payment_for_invoice(
            inv, bank_account_id=(data.get('bank_account_id') or '').strip(),
            payment_date=payment_date, user=request.user)
    except BridgeError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'ok': True, 'payment_id': str(p.pk), 'reference': p.reference,
        'amount': float(p.amount), 'status': p.status,
        'approval_status': getattr(p, 'approval_status', ''),
        'message': f'Payment raised for {p.reference}. It now needs two approvals '
                   f'(a Finance signature and a CFO/CEO signature) before it can be '
                   f'loaded to FNB — and it leaves only when you release it in the FNB app.',
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def link_firm_vendor(request, firm_id):
    """POST /api/v1/bonu/firms/<id>/link-vendor/ — link a firm to an existing vendor contact."""
    denied = _deny_finance(request) or _require_maker(request)
    if denied is not None:
        return denied
    from django.core.exceptions import ValidationError

    from billing.models import Contact
    from bonu.models import LawFirm
    from payments.models import Payment

    firm = LawFirm.objects.filter(pk=firm_id).first()
    if firm is None:
        return Response({'detail': 'That firm is not on file.'}, status=status.HTTP_404_NOT_FOUND)
    contact_id = (request.data or {}).get('contact_id')
    try:
        contact = (Contact.objects.filter(pk=contact_id, contact_type='vendor').first()
                   if contact_id else None)
    except (ValidationError, ValueError):
        contact = None
    if contact is None:
        return Response({'detail': 'Choose an existing VENDOR contact to link.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # Never move the vault link out from under payments already raised on the old
    # contact — that would split the firm's payment history and reset the dedupe key.
    if (firm.vendor_contact_id and firm.vendor_contact_id != contact.id
            and Payment.objects.filter(contact_id=firm.vendor_contact_id,
                                       reference__startswith='BONU ').exists()):
        return Response({'detail': f'{firm.name} already has BONU payments on its current '
                         'vendor record. Retire that vault account instead of re-linking.'},
                        status=status.HTTP_409_CONFLICT)
    firm.vendor_contact = contact
    firm.save(update_fields=['vendor_contact', 'updated_at'])
    return Response({'ok': True, 'firm': firm.name, 'contact': contact.name})
