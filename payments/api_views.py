"""payments/api_views.py"""
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import CanViewFinancials, CanExportEftBatch
from billing.models import Invoice
from .models import Payment, PaymentAllocation, PaymentBatch, PaymentBatchLine
from .serializers import (
    PaymentAllocationSerializer,
    PaymentDetailSerializer,
    PaymentListSerializer,
)
from .eft_export import build_fnb_bol
from . import batch_service
from django.utils import timezone


class PaymentViewSet(mixins.ListModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.CreateModelMixin,
                     mixins.UpdateModelMixin,
                     mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    # SECURITY (2026-07-17 audit + CFO directive): payments were readable by
    # ANY authenticated user across all entities. Restrict to finance +
    # management — CanViewFinancials already covers Finance titles, CFO, the
    # Executive (CEO) title, Operations Manager (COO) and Auditor (internal
    # auditor), plus admins/superusers. Operational/untitled staff are excluded.
    permission_classes = [IsAuthenticated, CanViewFinancials]
    queryset = Payment.objects.select_related(
        'contact', 'bank_account', 'currency_code', 'journal_entry'
    ).prefetch_related(
        'allocations__invoice', 'wht_record'
    ).order_by('-payment_date', '-created_at')
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields   = ['payment_number', 'contact__name', 'reference', 'description']
    ordering_fields = ['payment_number', 'payment_date', 'amount', 'status']

    def get_serializer_class(self):
        if self.action == 'list':
            return PaymentListSerializer
        return PaymentDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        pt = self.request.query_params.get('payment_type')
        if pt:
            qs = qs.filter(payment_type=pt)
        contact = self.request.query_params.get('contact')
        if contact:
            qs = qs.filter(contact_id=contact)
        # Bug 6631d0cf (Oprah, 2026-09-04): the Payment History table on an
        # individual vendor bill showed the same global payment list on every
        # bill. A bill's payments are those with an allocation to that invoice.
        invoice = self.request.query_params.get('invoice')
        if invoice:
            qs = qs.filter(allocations__invoice_id=invoice).distinct()
        from core.mixins import resolve_company_id_param
        company_id = resolve_company_id_param(self.request)
        if company_id:
            qs = qs.filter(company_id=company_id)
        from_date = self.request.query_params.get('from_date')
        if from_date:
            qs = qs.filter(payment_date__gte=from_date)
        to_date = self.request.query_params.get('to_date')
        if to_date:
            qs = qs.filter(payment_date__lte=to_date)
        return qs

    def _require_maker(self, request):
        """SoD (Workstream A #2): only a MAKER (Financial Controller / Senior
        Accountant / Accountant, or system automation) may ORIGINATE a payment.
        Finance Managers are the CHECKERS — they approve payments, they no
        longer create them. This removes the FM-creates + FM-approves collusion
        path the auditor flagged. Approval stays Finance-Manager-tier with the
        existing approver != creator/submitter rule in Payment.approve_payment."""
        from rest_framework.exceptions import PermissionDenied
        from core.models import get_user_profile
        prof = get_user_profile(request.user)
        if not (prof and prof.can_originate_controlled_txn):
            raise PermissionDenied(
                'Creating payments requires a maker title (Financial Controller / '
                'Senior Accountant / Accountant). Finance Managers approve payments, '
                'they do not raise them.')

    def create(self, request, *args, **kwargs):
        # SoD #2: authority-first — non-makers get 403 BEFORE payload validation.
        self._require_maker(request)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        self._require_maker(self.request)
        serializer.save()

    # Fable audit 2026-07-07: editing/deleting a payment is originating work.
    # Without these gates a Finance Manager (checker) could PATCH a pending
    # payment's amount and then approve the number they themselves set.
    def update(self, request, *args, **kwargs):
        self._require_maker(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._require_maker(request)
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='remittance-pdf')
    def remittance_pdf(self, request, pk=None):
        """Vendor-facing remittance advice PDF — one row per allocated bill.

        CFO directive 2026-05-24 (Track-B audit): eliminates the
        "which invoices did this lump-sum cover?" email loop.
        """
        from .remittance import render_remittance_pdf
        payment = self.get_object()
        pdf = render_remittance_pdf(payment)
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = (
            f'attachment; filename="remittance_{payment.payment_number}.pdf"'
        )
        return resp

    @action(detail=True, methods=['get'], url_path='remittance-csv')
    def remittance_csv(self, request, pk=None):
        """Same data, CSV format — for vendors that prefer machine-readable."""
        from .remittance import render_remittance_csv
        payment = self.get_object()
        csv_data = render_remittance_csv(payment)
        resp = HttpResponse(csv_data, content_type='text/csv')
        resp['Content-Disposition'] = (
            f'attachment; filename="remittance_{payment.payment_number}.csv"'
        )
        return resp

    @action(detail=True, methods=['post'], url_path='confirm')
    def confirm_payment(self, request, pk=None):
        payment = self.get_object()
        try:
            payment.confirm(user=request.user)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        # PAY-003 — prove AP aging still ties to BS after this post.
        from .models import record_bs_tie_check
        record_bs_tie_check(payment, user=request.user)
        serializer = PaymentDetailSerializer(payment, context={'request': request})
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # PAY-003 tier maker-checker endpoints
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='submit-for-approval')
    def submit_for_approval(self, request, pk=None):
        """Route a draft outbound payment into its amount-based tier queue."""
        self._require_maker(request)   # Fable audit: submitting is maker work
        payment = self.get_object()
        try:
            tier, role = payment.submit_for_approval(user=request.user)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = PaymentDetailSerializer(payment, context={'request': request})
        data = serializer.data
        data['assigned_tier'] = tier
        data['assigned_role'] = role
        return Response(data)

    @action(detail=False, methods=['get'], url_path='upload-template')
    def upload_template(self, request):
        """Download the reference CSV template for a bulk payment upload."""
        from .bulk_upload import template_csv
        resp = HttpResponse(template_csv(), content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="payment_upload_template.csv"'
        return resp

    @action(detail=False, methods=['post'], url_path='bulk-upload')
    def bulk_upload(self, request):
        """Bulk-create DRAFT payments from a pasted/uploaded sheet.

        Body: {text, bank_account, submit?, dry_run?}. dry_run=true returns the
        DeepSeek-mapped preview (no writes); otherwise creates drafts + submits
        each for approval. Maker-gated (same SoD as single create)."""
        self._require_maker(request)
        from . import bulk_upload as bu
        text = request.data.get('text') or ''
        if not text.strip():
            return Response({'detail': 'Paste or upload the payment rows first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(text.splitlines()) > 500:
            return Response({'detail': 'Too many rows — upload at most 500 '
                             'payments per batch.'},
                            status=status.HTTP_400_BAD_REQUEST)
        # Entity: explicit body value (FE sends the topbar pick) first — a
        # POST carries no ?company= auto-injection, so relying on
        # resolve_company_id_param alone 400s for read-all users and books
        # to the profile default for everyone else (Fable audit).
        from core.mixins import resolve_company_id_param
        company_id = (self._company_from_body(request.data)
                      or resolve_company_id_param(request))
        if request.data.get('dry_run'):
            try:
                return Response(bu.build_preview(text, company_id=company_id))
            except Exception as e:  # noqa: BLE001
                return Response({'detail': f'Could not read the sheet: {e}'},
                                status=status.HTTP_400_BAD_REQUEST)
        if not company_id:
            return Response({'detail': 'Select a company/entity (top-right) first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            out = bu.create_payments(
                text, user=request.user, company_id=company_id,
                bank_account_id=request.data.get('bank_account'),
                submit=request.data.get('submit', True),
            )
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if not out.get('created_count'):
            # Nothing created — don't hand the FE a green success card.
            return Response(out, status=status.HTTP_400_BAD_REQUEST)
        return Response(out, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='read-invoice',
            parser_classes=[MultiPartParser, FormParser])
    def read_invoice(self, request):
        """POST /api/v1/payments/read-invoice/ — read an uploaded invoice and
        return pre-filled fields (total due, payee bank account, etc.) for the
        operator to CHECK before creating the payment. Saves nothing
        (CFO 2026-08-22, raised by Tlamelo). Maker-gated like any payment entry."""
        self._require_maker(request)
        f = request.FILES.get('file')
        if f is None:
            return Response({'detail': 'Attach the invoice as "file".'},
                            status=status.HTTP_400_BAD_REQUEST)
        if getattr(f, 'size', 0) > 20 * 1024 * 1024:
            return Response({'detail': 'File too large (max 20 MB).'},
                            status=status.HTTP_400_BAD_REQUEST)
        from .invoice_read import read_invoice as _read
        data = _read(f.read(), mime=getattr(f, 'content_type', '') or '',
                     filename=f.name)
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='once-off')
    def once_off(self, request):
        """Create a ONE-OFF (ad-hoc) payment to a payee NOT in the vendor master.

        The destination bank is captured inline (no Vendor Bank register entry);
        `contact` points at a per-company sentinel 'Ad-hoc / One-off Payee'. It
        still runs the full dual-control (1-FM/FC + 1-CFO/CEO) approval quorum and is flagged is_once_off.
        Maker-gated (same SoD as any payment create)."""
        self._require_maker(request)
        from decimal import Decimal, InvalidOperation
        from django.utils import timezone
        from billing.models import Contact
        from ledger.models import Account
        from core.mixins import resolve_company_id_param

        def bad(msg):
            return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)

        d = request.data
        payee = (d.get('payee_name') or '').strip()
        acct = (d.get('account_number') or '').strip()
        if not payee:
            return bad('Payee name is required.')
        if not acct:
            return bad("Payee's bank account number is required.")
        from .models import resolve_paying_bank, resolve_fx_rate, default_pop_email
        bank = resolve_paying_bank(d.get('bank_account'))
        if not bank:
            return bad('Pick a valid paying bank account.')
        # Company/entity: explicit body value (the FE sends the topbar pick)
        # → query/header/profile → else infer from the paying bank's owner.
        # A one-off is always booked in the entity that OWNS the paying bank
        # (CFO 2026-07-07, Pako's "SELECTING ENTITY ERROR").
        selected_id = self._company_from_body(d) or resolve_company_id_param(request)
        bank_company_id = str(bank.owner_company_id) if bank.owner_company_id else None
        company_id = bank_company_id or selected_id
        if not company_id:
            return bad('Select a company/entity (top-right) first.')
        if selected_id and bank_company_id and selected_id != bank_company_id:
            return bad('That paying bank belongs to a different entity than the '
                       'one selected top-right — pick a bank in this entity.')
        try:
            amount = Decimal(str(d.get('amount')))
        except (InvalidOperation, TypeError):
            return bad('Amount must be a number.')
        if amount <= 0:
            return bad('Amount must be greater than zero.')
        # Method must be a real model choice — an invalid value (e.g. 'rtgs')
        # would save unvalidated and then dodge the electronic-payment gates.
        method = (d.get('payment_method') or 'bank_transfer').strip()
        if method not in Payment.PaymentMethod.values:
            return bad(f'Unsupported payment method "{method}".')
        # Currency must exist + be active; non-BWP needs an APPROVED FX rate —
        # never book USD 10,000 as BWP 10,000 via a silent rate of 1.0.
        from core.models import Currency
        currency = (d.get('currency') or 'BWP').strip().upper()
        if not Currency.objects.filter(pk=currency, is_active=True).exists():
            return bad(f'Unknown or inactive currency "{currency}".')
        pay_date = timezone.localdate()
        rate = resolve_fx_rate(currency, pay_date)
        if rate is None:
            return bad(f'No approved {currency}→BWP exchange rate is loaded. '
                       'Load and approve one on /fx first.')
        try:
            with transaction.atomic():
                # get_or_create is unconstrained on (company, name) — recover
                # from historical duplicates instead of 500ing.
                sentinel = Contact.objects.filter(
                    company_id=company_id, contact_type='vendor',
                    name='Ad-hoc / One-off Payee').order_by('created_at').first()
                if sentinel is None:
                    sentinel = Contact.objects.create(
                        company_id=company_id, contact_type='vendor',
                        name='Ad-hoc / One-off Payee', currency_code_id='BWP')
                p = Payment(
                    payment_type=Payment.PaymentType.SENT, contact=sentinel, company_id=company_id,
                    bank_account=bank, payment_date=pay_date,
                    currency_code_id=currency, exchange_rate=rate,
                    amount=amount, payment_method=method,
                    # ASCII hyphen, not an em dash: this string reaches FNB as
                    # remittanceInformationUnstructured and U+2014 is outside
                    # RMB spec V-03 §2.5 — it cost an RR10 reject on 2026-08-19.
                    reference=(d.get('reference') or f'One-off - {payee}')[:200],
                    description=(d.get('description') or '')[:1000],
                    is_once_off=True, payee_name=payee[:200],
                    payee_bank_name=(d.get('bank_name') or '')[:120],
                    payee_account_number=acct[:40], payee_branch_code=(d.get('branch_code') or '')[:20],
                    # What the bank is told, when the operator chooses it
                    # (CFO 2026-08-20). Blank keeps the old derivation. The
                    # lengths are FNB's ISO 20022 limits; the values are folded
                    # to FNB's character set at send time, not here, so one
                    # rule governs every path to the bank.
                    bank_beneficiary_name=(d.get('bank_beneficiary_name') or '')[:140],
                    bank_our_reference=(d.get('bank_our_reference') or '')[:35],
                    bank_narration=(d.get('bank_narration') or '')[:140],
                    # Where FNB emails the POP. Typed value wins; blank falls to
                    # the Accounts default (CFO 2026-08-22).
                    remittance_email=(d.get('remittance_email') or '').strip()[:254]
                                     or default_pop_email(),
                    created_by=request.user,
                )
                p.save(audit_user=request.user)
                if d.get('submit', True):
                    # In-transaction: a submit failure rolls the draft back
                    # too, so a red error never leaves an orphan draft behind.
                    p.submit_for_approval(user=request.user)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return bad(msg)
        return Response(PaymentDetailSerializer(p, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    @staticmethod
    def _company_from_body(d):
        """Resolve an explicit company (UUID or code) from a POST body."""
        raw = (str(d.get('company') or '')).strip()
        if not raw:
            return None
        from core.models import Company
        from django.db.models import Q
        try:
            row = Company.objects.filter(Q(pk=raw)).first()
        except Exception:   # noqa: BLE001 — non-UUID string
            row = None
        if row is None:
            row = Company.objects.filter(code__iexact=raw).first()
        return str(row.pk) if row else None

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Approve a pending payment (mandatory comment) and post DR AP / CR Bank."""
        payment = self.get_object()
        comment = request.data.get('comment') or request.data.get('approval_comment') or ''
        try:
            result = payment.approve_payment(user=request.user, comment=comment) or {}
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        payment.refresh_from_db()
        serializer = PaymentDetailSerializer(payment, context={'request': request})
        data = serializer.data
        data['quorum'] = result
        # Outbound quorum not yet met — the signature is recorded but nothing
        # posted; do NOT run the BS-tie check (there is no GL entry yet).
        if result.get('quorum_met') is False:
            data['quorum_pending'] = True
            return Response(data)
        # Posted — prove AP aging still ties to BS after the GL post.
        from .models import record_bs_tie_check
        recon = record_bs_tie_check(payment, user=request.user)
        data['bs_tie'] = {
            'reconciled': bool(recon.get('reconciled')),
            'variance':   str(recon.get('variance')) if recon else None,
        }
        return Response(data)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """Reject a pending payment (mandatory comment). Leaves it at draft."""
        payment = self.get_object()
        comment = request.data.get('comment') or request.data.get('approval_comment') or ''
        try:
            payment.reject_payment(user=request.user, comment=comment)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = PaymentDetailSerializer(payment, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='approval-queue')
    def approval_queue(self, request):
        """Payments awaiting tier approval. Company-scoped via the topbar."""
        from core.mixins import resolve_company_id_param
        qs = (Payment.objects
              .filter(approval_status=Payment.ApprovalStatus.PENDING)
              .select_related('contact', 'bank_account', 'currency_code',
                              'submitted_for_approval_by')
              .order_by('-submitted_for_approval_at'))
        company_id = resolve_company_id_param(request)
        if company_id:
            qs = qs.filter(company_id=company_id)
        serializer = PaymentDetailSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='create-from-bill')
    def create_from_bill(self, request):
        """PAY-003 init path #1 — "Pay" on a Posted vendor bill.

        Creates a draft SENT payment + allocation against the bill, then
        submits it into the tier approval queue. Body:
          {bill_id, bank_account_id, payment_date,
           amount(optional=outstanding), reference(optional), payment_method}
        """
        self._require_maker(request)  # SoD #2 — only makers originate payments
        from decimal import Decimal
        from ledger.models import Account
        bill_id   = request.data.get('bill_id')
        bank_id   = request.data.get('bank_account_id')
        pay_date  = request.data.get('payment_date')
        amount_in = request.data.get('amount')
        reference = (request.data.get('reference') or '').strip()
        method    = request.data.get('payment_method') or Payment.PaymentMethod.BANK_TRANSFER

        if not bill_id or not bank_id or not pay_date:
            return Response(
                {'error': 'bill_id, bank_account_id and payment_date are required.'},
                status=status.HTTP_400_BAD_REQUEST)
        try:
            bill = Invoice.objects.get(pk=bill_id)
        except Invoice.DoesNotExist:
            return Response({'error': 'Bill not found.'}, status=status.HTTP_404_NOT_FOUND)
        if bill.invoice_type != Invoice.InvoiceType.VENDOR_BILL:
            return Response({'error': 'Only vendor bills can be paid here.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if bill.status not in (Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID):
            return Response(
                {'error': f'Bill must be Posted to pay (current: {bill.get_status_display()}).'},
                status=status.HTTP_400_BAD_REQUEST)
        try:
            bank = Account.objects.get(pk=bank_id)
        except Account.DoesNotExist:
            return Response({'error': 'Bank account not found.'},
                            status=status.HTTP_404_NOT_FOUND)

        outstanding = (bill.total_amount or Decimal('0')) - (bill.amount_paid or Decimal('0'))
        amount = Decimal(str(amount_in)) if amount_in is not None else outstanding
        if amount <= 0:
            return Response({'error': 'Payment amount must be greater than zero.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if amount > outstanding + Decimal('0.01'):
            return Response(
                {'error': f'Amount {amount} exceeds outstanding {outstanding} on the bill.'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                # Remember-the-POP (CFO 2026-08-22): use the vendor's remembered
                # POP email if their default bank account has one; else the model
                # default (Accounts) applies.
                from procurement.models import VendorBankAccount
                _vba = (VendorBankAccount.objects
                        .filter(contact=bill.contact, status=VendorBankAccount.Status.ACTIVE)
                        .exclude(email='')
                        .order_by('-is_default', '-approved_at').first())
                payment = Payment(
                    payment_type   = Payment.PaymentType.SENT,
                    contact        = bill.contact,
                    company        = bill.company,
                    bank_account   = bank,
                    payment_date   = pay_date,
                    currency_code_id = bill.currency_code_id or 'BWP',
                    exchange_rate  = bill.exchange_rate or Decimal('1.00000000'),
                    amount         = amount,
                    payment_method = method,
                    reference      = reference or f"Payment for {bill.invoice_number}",
                    description    = f"PAY-003 bill payment — {bill.invoice_number}",
                    created_by     = request.user,
                    **({'remittance_email': _vba.email} if _vba else {}),
                )
                payment.save(audit_user=request.user)
                PaymentAllocation.objects.create(
                    payment=payment, invoice=bill, amount_allocated=amount)
                tier, role = payment.submit_for_approval(user=request.user)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PaymentDetailSerializer(payment, context={'request': request})
        data = serializer.data
        data['assigned_tier'] = tier
        data['assigned_role'] = role
        return Response(data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Bulk-action support: destroy, duplicate, reset_to_draft
    # ------------------------------------------------------------------

    def destroy(self, request, *args, **kwargs):
        """Only drafts can be deleted — confirmed payments have GL entries."""
        self._require_maker(request)
        instance = self.get_object()
        if instance.status != Payment.Status.DRAFT:
            return Response(
                {'error': (
                    f"Cannot delete {instance.payment_number}: status is "
                    f"{instance.status}. Only draft payments can be deleted; "
                    "reset confirmed payments to draft first."
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='duplicate')
    def duplicate_payment(self, request, pk=None):
        """Clone a payment as a fresh draft. Maker-gated — duplicating IS
        originating (Fable audit: an FM could otherwise raise payments via
        duplicate despite the SoD block on create)."""
        self._require_maker(request)
        original = self.get_object()
        copy = Payment(
            payment_type=original.payment_type,
            contact=original.contact,
            company=original.company,               # keep the entity — a company-less
                                                     # clone vanishes from every topbar view
            bank_account=original.bank_account,
            vendor_bank_account=original.vendor_bank_account,
            payment_date=original.payment_date,
            currency_code=original.currency_code,
            exchange_rate=original.exchange_rate,
            amount=original.amount,
            payment_method=original.payment_method,
            is_once_off=original.is_once_off,
            payee_name=original.payee_name,
            payee_bank_name=original.payee_bank_name,
            payee_account_number=original.payee_account_number,
            payee_branch_code=original.payee_branch_code,
            reference=f"Copy of {original.reference}"[:200],
            description=(
                f"Copy of {original.payment_number}"
                + (f" — {original.description}" if original.description else "")
            )[:1000],
            created_by=request.user,
        )
        copy.save(audit_user=request.user)
        serializer = PaymentDetailSerializer(copy, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='reset-to-draft')
    def reset_to_draft(self, request, pk=None):
        """
        Un-confirm a confirmed payment: reverse its journal entry and revert
        status to draft. Refused if any allocation exists.
        """
        self._require_maker(request)   # Fable audit: was callable by anyone
        payment = self.get_object()
        if payment.status != Payment.Status.CONFIRMED:
            return Response(
                {'error': f'Only confirmed payments can be reset to draft (status: {payment.status}).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if payment.allocations.exists():
            return Response(
                {'error': (
                    'This payment has invoice allocations. '
                    'Remove the allocations before resetting to draft.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            if payment.journal_entry_id:
                je = payment.journal_entry
                if je.status == je.Status.POSTED:
                    # A reversal now lands in the period of the entry it reverses, so it
                    # can be refused by a closed period or the historical lock. Say that
                    # in plain words — an unhandled ValidationError here is a bare 500.
                    try:
                        je.reverse(user=request.user,
                                   reason=f"Reset {payment.payment_number} to draft")
                    except ValidationError as exc:
                        return Response(
                            {'detail': '; '.join(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
            # Fable audit 2026-07-07: a reset payment must NOT keep its
            # APPROVED badge or its signatures — otherwise it can be edited
            # and re-posted with zero fresh approvals (full quorum bypass).
            Payment.objects.filter(pk=payment.pk).update(
                status=Payment.Status.DRAFT,
                journal_entry=None,
                approval_status=Payment.ApprovalStatus.NOT_REQUIRED,
                approval_tier=None,
                approval_comment='',
                submitted_for_approval_by=None,
                submitted_for_approval_at=None,
                approval_decided_by=None,
                approval_decided_at=None,
            )
            from .models import PaymentApproval
            PaymentApproval.objects.filter(payment=payment).delete()

        payment.refresh_from_db()
        serializer = PaymentDetailSerializer(payment, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='allocate')
    def allocate(self, request, pk=None):
        payment = self.get_object()
        invoice_id = request.data.get('invoice_id')
        amount     = request.data.get('amount')

        if not invoice_id or amount is None:
            return Response(
                {'error': 'Both invoice_id and amount are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            invoice = Invoice.objects.get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return Response({'error': 'Invoice not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            from decimal import Decimal
            allocation = PaymentAllocation.objects.create(
                payment          = payment,
                invoice          = invoice,
                amount_allocated = Decimal(str(amount)),
            )
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PaymentAllocationSerializer(allocation, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Pay-run (PaymentBatch) endpoints
    # ------------------------------------------------------------------

    @action(detail=False, methods=['post'], url_path='propose-pay-run')
    def propose_pay_run(self, request):
        """
        POST /api/v1/payments/propose-pay-run/

        Body:
          {
            "company_id":         <uuid>,                # required
            "run_date":           "2026-05-30",          # required (YYYY-MM-DD)
            "bank_account_id":    <uuid>,                # required (ledger.Account)
            "due_through_date":   "2026-05-31"           # optional, defaults to run_date
          }

        Returns the proposed PaymentBatch (id, status, total_bwp, lines[]).
        """
        from core.models import Company
        from ledger.models import Account

        company_id        = request.data.get('company_id')
        run_date_str      = request.data.get('run_date')
        bank_account_id   = request.data.get('bank_account_id')
        due_through_str   = request.data.get('due_through_date') or run_date_str

        if not company_id or not run_date_str or not bank_account_id:
            return Response(
                {'error': 'company_id, run_date, and bank_account_id are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            run_date         = datetime.date.fromisoformat(run_date_str)
            due_through_date = datetime.date.fromisoformat(due_through_str)
        except (TypeError, ValueError):
            return Response(
                {'error': 'run_date and due_through_date must be ISO YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            company = Company.objects.get(pk=company_id)
        except Company.DoesNotExist:
            return Response({'error': 'Company not found.'},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            bank_account = Account.objects.get(pk=bank_account_id)
        except Account.DoesNotExist:
            return Response({'error': 'Bank account not found.'},
                            status=status.HTTP_404_NOT_FOUND)
        if not bank_account.is_bank_account:
            return Response(
                {'error': f'Account {bank_account.code} is not flagged is_bank_account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            batch = batch_service.propose_pay_run(
                company           = company,
                run_date          = run_date,
                bank_account      = bank_account,
                due_through_date  = due_through_date,
                user              = request.user,
            )
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            _serialize_pay_run_batch(batch),
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False, methods=['post'],
        url_path=r'commit-pay-run/(?P<batch_id>[0-9a-f-]{36})',
    )
    def commit_pay_run(self, request, batch_id=None):
        """
        POST /api/v1/payments/commit-pay-run/<batch_id>/

        Creates one Payment + allocation per included line and posts each.
        Returns the committed batch with payment ids populated on each line.
        """
        try:
            batch = PaymentBatch.objects.get(pk=batch_id)
        except PaymentBatch.DoesNotExist:
            return Response({'error': 'PaymentBatch not found.'},
                            status=status.HTTP_404_NOT_FOUND)

        try:
            batch = batch_service.commit_batch(batch, user=request.user)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_serialize_pay_run_batch(batch))


def _serialize_pay_run_batch(batch: PaymentBatch) -> dict:
    """Lightweight inline serializer for PaymentBatch + lines."""
    return {
        'id':            str(batch.pk),
        'company_id':    str(batch.company_id) if batch.company_id else None,
        'run_date':      batch.run_date.isoformat() if batch.run_date else None,
        'bank_account_id': str(batch.bank_account_id) if batch.bank_account_id else None,
        'status':        batch.status,
        'eft_file_name': batch.eft_file_name,
        'total_bwp':     str(batch.total_bwp),
        'committed_at':  batch.committed_at.isoformat() if batch.committed_at else None,
        'created_at':    batch.created_at.isoformat(),
        'lines': [
            {
                'id':              str(ln.pk),
                'invoice_id':      str(ln.invoice_id),
                'invoice_number':  ln.invoice.invoice_number,
                'amount_proposed': str(ln.amount_proposed),
                'amount_discount': str(ln.amount_discount),
                'included':        ln.included,
                'payment_id':      str(ln.payment_id) if ln.payment_id else None,
            }
            for ln in batch.lines.select_related('invoice').all()
        ],
    }


class PaymentAllocationViewSet(mixins.ListModelMixin,
                                mixins.RetrieveModelMixin,
                                mixins.CreateModelMixin,
                                viewsets.GenericViewSet):
    # Same finance-only gate as PaymentViewSet — allocations expose payment
    # amounts + invoice links (2026-07-17 audit).
    permission_classes = [IsAuthenticated, CanViewFinancials]
    queryset = PaymentAllocation.objects.select_related(
        'payment', 'invoice'
    ).order_by('-created_at')
    serializer_class = PaymentAllocationSerializer
    filter_backends  = [filters.SearchFilter]
    search_fields    = ['payment__payment_number', 'invoice__invoice_number']

    def get_queryset(self):
        qs = super().get_queryset()
        payment = self.request.query_params.get('payment')
        if payment:
            qs = qs.filter(payment_id=payment)
        invoice = self.request.query_params.get('invoice')
        if invoice:
            qs = qs.filter(invoice_id=invoice)
        return qs


# ---------------------------------------------------------------------------
# EFT batch export — FNB BOL
# ---------------------------------------------------------------------------

class EFTBatchExportView(APIView):
    """
    POST /api/v1/payments/eft-export/

    Body: {
      "payment_ids":          ["<uuid>", "<uuid>", ...],
      "source_account_number": "62123456789",
      "batch_ref":            "PAYRUN-2026-05-10",
      "format":               "fnb_bol"   // optional; default fnb_bol
    }

    Returns:
      - text/plain body with the bank-format file (Content-Disposition attachment)
      - X-EFT-Summary header (JSON) with record_count, total_cents, skipped[]

    Only confirmed outbound payments with an ACTIVE vendor bank account
    are written. Anything excluded is reported in skipped[]. The CFO
    must validate the format against an FNB-supplied sample before live use.

    SECURITY: this file carries UNMASKED vendor bank account numbers, so it is
    gated to outbound-payment makers + finance management (CanExportEftBatch),
    matching the other outbound-payment / FNB views. It previously had no
    permission_classes and fell back to the DRF default (any authenticated user).
    """

    permission_classes = [IsAuthenticated, CanExportEftBatch]

    def post(self, request):
        ids = request.data.get('payment_ids') or []
        source_account = (request.data.get('source_account_number') or '').strip()
        batch_ref      = (request.data.get('batch_ref') or 'PAYRUN').strip()
        fmt            = (request.data.get('format') or 'fnb_bol').lower()

        if not ids:
            return Response({'error': 'payment_ids is required.'}, status=400)
        if not source_account:
            return Response({'error': 'source_account_number is required.'}, status=400)
        if fmt != 'fnb_bol':
            return Response({'error': f'Format "{fmt}" not supported yet. Only fnb_bol is implemented.'}, status=400)

        payments = list(
            Payment.objects
            .filter(pk__in=ids)
            .select_related('contact', 'vendor_bank_account')
        )
        if not payments:
            return Response({'error': 'No matching payments found.'}, status=404)

        run_date = timezone.localdate()
        text, summary = build_fnb_bol(
            payments,
            source_account_number=source_account,
            run_date=run_date,
            batch_ref=batch_ref,
        )

        if summary['record_count'] == 0:
            return Response(
                {'error': 'No eligible payments — all rows were skipped.', 'summary': summary},
                status=400,
            )

        filename = f"fnb_bol_{run_date.strftime('%Y%m%d')}_{batch_ref}.txt"
        resp = HttpResponse(text, content_type='text/plain; charset=ascii')
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        import json as _json
        resp['X-EFT-Summary'] = _json.dumps(summary)
        return resp
