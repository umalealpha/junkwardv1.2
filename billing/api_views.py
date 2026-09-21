"""billing/api_views.py"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.mixins import CompanyScopedViewSetMixin
from .models import Contact, Invoice, InvoiceLine, ReverseChargeEntry
from .serializers import (
    ContactSerializer,
    InvoiceDetailSerializer,
    InvoiceListSerializer,
    ReverseChargeEntrySerializer,
)


_REVERSAL_TYPE = {
    Invoice.InvoiceType.CUSTOMER_INVOICE: Invoice.InvoiceType.CREDIT_NOTE,
    Invoice.InvoiceType.VENDOR_BILL:      Invoice.InvoiceType.VENDOR_CREDIT,
}


class ContactViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Contact.objects.select_related('currency_code').order_by('name')
    serializer_class = ContactSerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['name', 'email', 'registration_number', 'tax_id', 'graphite_id']
    ordering_fields  = ['name', 'contact_type', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        ct = self.request.query_params.get('contact_type')
        if ct:
            qs = qs.filter(contact_type=ct)
        active = self.request.query_params.get('is_active')
        if active is not None:
            qs = qs.filter(is_active=active.lower() == 'true')
        # Company filter is applied by CompanyScopedViewSetMixin (UUID + code).
        return qs

    # create/update handled by ContactSerializer, EXCEPT: a Finance Manager
    # (approver-only) must not enter or edit VENDOR master data — CFO directive
    # 2026-07-05 ('he must not input any data; real control is essential').
    # Customers / brokers / reinsurers are unaffected (not a finance control).
    def _block_fm_on_vendor(self, contact_type):
        from core.models import user_is_approver_only
        if contact_type == Contact.ContactType.VENDOR and user_is_approver_only(self.request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(
                'A Finance Manager approves vendor records, but does not create or edit '
                'them. Vendor master data is entered by an Accountant / Senior Accountant '
                '/ Financial Controller.')

    def perform_create(self, serializer):
        self._block_fm_on_vendor(serializer.validated_data.get('contact_type'))
        serializer.save()

    def perform_update(self, serializer):
        new_type = serializer.validated_data.get(
            'contact_type', getattr(serializer.instance, 'contact_type', None))
        self._block_fm_on_vendor(new_type)
        serializer.save()


class InvoiceViewSet(CompanyScopedViewSetMixin,
                     mixins.ListModelMixin,
                     mixins.RetrieveModelMixin,
                     mixins.CreateModelMixin,
                     mixins.UpdateModelMixin,
                     mixins.DestroyModelMixin,
                     viewsets.GenericViewSet):
    # CFO structural audit 2026-05-19: Invoice has no direct company FK;
    # scope via contact.company instead.
    company_lookup_field = 'contact__company_id'
    queryset = Invoice.objects.select_related(
        'contact', 'currency_code', 'journal_entry'
    ).prefetch_related('lines__account', 'lines__tax_code').order_by('-created_at')
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields   = ['invoice_number', 'contact__name', 'description']
    ordering_fields = ['invoice_number', 'issue_date', 'due_date', 'total_amount', 'status']

    def get_serializer_class(self):
        if self.action == 'list':
            return InvoiceListSerializer
        return InvoiceDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        it = self.request.query_params.get('invoice_type')
        if it:
            qs = qs.filter(invoice_type=it)
        contact = self.request.query_params.get('contact')
        if contact:
            qs = qs.filter(contact_id=contact)
        # Bills raised against a specific PO — the Match-bill picker on a
        # purchase order lists the vendor bills captured for that PO (they carry
        # Invoice.purchase_order). Filtering by supplier alone returned every
        # bill of the vendor, or none where the vendor contact had no company,
        # so the picker showed nothing (Omogomotsi, 2026-09-02).
        po = self.request.query_params.get('purchase_order')
        if po:
            qs = qs.filter(purchase_order_id=po)
        # Company filter is applied by CompanyScopedViewSetMixin via
        # `company_lookup_field='contact__company_id'`.
        from_date = self.request.query_params.get('from_date')
        if from_date:
            qs = qs.filter(issue_date__gte=from_date)
        to_date = self.request.query_params.get('to_date')
        if to_date:
            qs = qs.filter(issue_date__lte=to_date)
        return qs

    @action(detail=True, methods=['post'], url_path='post')
    def post_invoice(self, request, pk=None):
        invoice = self.get_object()
        try:
            invoice.post(user=request.user)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        serializer = InvoiceDetailSerializer(invoice, context={'request': request})
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # Bill approval workflow actions (vendor_bill only)
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='submit-for-approval')
    def submit_for_approval(self, request, pk=None):
        invoice = self.get_object()
        try:
            invoice.submit_for_approval(user=request.user)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceDetailSerializer(invoice, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='approve-bill')
    def approve_bill(self, request, pk=None):
        invoice = self.get_object()
        try:
            invoice.approve_bill(user=request.user)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceDetailSerializer(invoice, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='reject-bill')
    def reject_bill(self, request, pk=None):
        invoice = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'error': 'A rejection reason is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            invoice.reject_bill(user=request.user, reason=reason)
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InvoiceDetailSerializer(invoice, context={'request': request}).data)

    # ------------------------------------------------------------------
    # Bulk-actions support: duplicate, reverse, destroy
    # ------------------------------------------------------------------

    def destroy(self, request, *args, **kwargs):
        """Only drafts can be deleted — posted invoices have GL entries."""
        instance = self.get_object()
        if instance.status != Invoice.Status.DRAFT:
            return Response(
                {'error': (
                    f"Cannot delete {instance.invoice_number}: status is "
                    f"{instance.status}. Only draft invoices can be deleted; "
                    "reverse posted invoices instead."
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='duplicate')
    def duplicate_invoice(self, request, pk=None):
        """Clone an invoice (header + lines) as a fresh draft."""
        original = self.get_object()
        with transaction.atomic():
            copy = Invoice(
                invoice_type=original.invoice_type,
                contact=original.contact,
                issue_date=original.issue_date,
                due_date=original.due_date,
                currency_code=original.currency_code,
                exchange_rate=original.exchange_rate,
                description=(
                    f"Copy of {original.invoice_number}"
                    + (f" — {original.description}" if original.description else "")
                )[:1000],
                source_type='duplicate',
                source_id=str(original.pk),
                created_by=request.user,
            )
            # Note: invoice_number auto-generates, status defaults to draft
            copy.save(audit_user=request.user)

            for ln in original.lines.all():
                InvoiceLine.objects.create(
                    invoice=copy,
                    account=ln.account,
                    description=ln.description,
                    quantity=ln.quantity,
                    unit_price=ln.unit_price,
                    tax_code=ln.tax_code,
                )
            copy.recalculate_totals()
            copy.save(audit_user=request.user)

        serializer = InvoiceDetailSerializer(copy, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='reset-to-draft')
    def reset_to_draft(self, request, pk=None):
        """
        Un-post a posted invoice: reverse its journal entry (so the GL nets out)
        and set status back to draft so the user can edit and re-post.

        Refused if the invoice has any payment allocated — those would have to
        be unallocated first.
        """
        invoice = self.get_object()
        if invoice.status != Invoice.Status.POSTED:
            return Response(
                {'error': f'Only posted invoices can be reset to draft (status: {invoice.status}).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if invoice.allocations.exists():
            return Response(
                {'error': (
                    'This invoice has payment allocations. '
                    'Cancel/reverse the related payments first, then reset to draft.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Reverse the underlying JE so the books balance back out.
            if invoice.journal_entry_id:
                je = invoice.journal_entry
                if je.status == je.Status.POSTED:
                    # A reversal now lands in the period of the entry it reverses, so it
                    # can be refused by a closed period or the historical lock. Say that
                    # in plain words — an unhandled ValidationError here is a bare 500.
                    try:
                        je.reverse(user=request.user,
                                   reason=f"Reset {invoice.invoice_number} to draft")
                    except ValidationError as exc:
                        return Response(
                            {'detail': '; '.join(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

            # Bypass the model's immutability guard with a direct UPDATE.
            Invoice.objects.filter(pk=invoice.pk).update(
                status=Invoice.Status.DRAFT,
                journal_entry=None,
            )

        invoice.refresh_from_db()
        serializer = InvoiceDetailSerializer(invoice, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='reverse')
    def reverse_invoice(self, request, pk=None):
        """
        Create a credit note (or vendor credit) reversing this posted invoice.
        Only posted invoices can be reversed; only customer_invoice / vendor_bill
        types have a defined reversal counterpart.
        """
        original = self.get_object()
        if original.status != Invoice.Status.POSTED:
            return Response(
                {'error': f'Only posted invoices can be reversed (status: {original.status}).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rev_type = _REVERSAL_TYPE.get(original.invoice_type)
        if not rev_type:
            return Response(
                {'error': f'No reversal type defined for {original.invoice_type}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = (request.data.get('reason') or '').strip()
        with transaction.atomic():
            reversal = Invoice(
                invoice_type=rev_type,
                contact=original.contact,
                issue_date=original.issue_date,
                due_date=original.issue_date,
                currency_code=original.currency_code,
                exchange_rate=original.exchange_rate,
                description=(
                    f"Reversal of {original.invoice_number}"
                    + (f" — {reason}" if reason else "")
                )[:1000],
                source_type='reversal',
                source_id=str(original.pk),
                created_by=request.user,
            )
            reversal.save(audit_user=request.user)

            for ln in original.lines.all():
                InvoiceLine.objects.create(
                    invoice=reversal,
                    account=ln.account,
                    description=f"Reversal: {ln.description}"[:500],
                    quantity=ln.quantity,
                    unit_price=-Decimal(ln.unit_price),
                    tax_code=ln.tax_code,
                )
            reversal.recalculate_totals()
            reversal.save(audit_user=request.user)

        serializer = InvoiceDetailSerializer(reversal, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# ReverseChargeEntry — reverse-charge VAT on imported remote services
# (VAT Amendment Act No.16 of 2025, effective 1 June 2026)
# ---------------------------------------------------------------------------

class ReverseChargeEntryViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    # ReverseChargeEntry has a direct `company` FK, so the mixin's default
    # company_lookup_field='company_id' applies with no override needed.
    queryset = ReverseChargeEntry.objects.select_related(
        'company', 'created_by',
    ).order_by('-invoice_date', '-created_at')
    serializer_class = ReverseChargeEntrySerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['vendor', 'note']
    ordering_fields  = ['invoice_date', 'bwp_amount', 'output_vat', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        from_date = self.request.query_params.get('from_date')
        if from_date:
            qs = qs.filter(invoice_date__gte=from_date)
        to_date = self.request.query_params.get('to_date')
        if to_date:
            qs = qs.filter(invoice_date__lte=to_date)
        # Company filter is applied by CompanyScopedViewSetMixin via the
        # default company_lookup_field='company_id'.
        return qs

    def perform_destroy(self, instance):
        # M-2 (Fable-5 review): destroy was ungated and unattributed — any
        # user could delete a reverse-charge entry with no audit trail of
        # who did it. Unlike procurement's PurchaseOrder or commissions'
        # CommissionSubmission, ReverseChargeEntry has no filed/locked-period
        # concept yet (there is no posting/approval workflow here — entries
        # only feed build_vat_return's read-only aggregation), so there is
        # nothing to gate on; the fix is attribution, not a status lock.
        # AuditableMixin.delete() (core/models.py) writes the DELETE
        # AuditLog row with this as its `user` — same pattern as
        # ledger/api_views.py's delete_attachment and petty_cash's
        # delete_receipt.
        instance.delete(audit_user=self.request.user)
