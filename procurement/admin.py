from django.contrib import admin

from .models import (
    BillAIVerification,
    ClaimsAssessment,
    GoodsReceiptNote,
    GoodsReceiptNoteLine,
    POBillMatch,
    PurchaseOrder,
    PurchaseOrderLine,
    VariancePolicy,
    VendorBankAccount,
)


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display  = ('po_number', 'department', 'supplier', 'issue_date',
                     'currency_code', 'total_amount', 'total_bwp', 'status')
    list_filter   = ('status', 'department', 'currency_code')
    search_fields = ('po_number', 'supplier__name', 'related_claim_reference')
    inlines       = [PurchaseOrderLineInline]


class GoodsReceiptNoteLineInline(admin.TabularInline):
    model = GoodsReceiptNoteLine
    extra = 0


@admin.register(GoodsReceiptNote)
class GoodsReceiptNoteAdmin(admin.ModelAdmin):
    list_display  = ('grn_number', 'purchase_order', 'receipt_date',
                     'received_by', 'status')
    list_filter   = ('status',)
    search_fields = ('grn_number', 'purchase_order__po_number',
                     'delivery_note_reference')
    inlines       = [GoodsReceiptNoteLineInline]


@admin.register(POBillMatch)
class POBillMatchAdmin(admin.ModelAdmin):
    list_display  = ('purchase_order', 'bill', 'match_status',
                     'price_variance', 'matched_by', 'matched_at')
    list_filter   = ('match_status',)
    search_fields = ('purchase_order__po_number', 'bill__invoice_number')


@admin.register(VendorBankAccount)
class VendorBankAccountAdmin(admin.ModelAdmin):
    list_display  = ('contact', 'bank_name', 'account_number', 'currency_code',
                     'is_default', 'status', 'approved_by')
    list_filter   = ('status', 'currency_code', 'is_default')
    search_fields = ('contact__name', 'bank_name', 'account_holder_name',
                     'account_number')
    readonly_fields = ('submitted_at', 'approved_at', 'rejected_at', 'retired_at')


@admin.register(VariancePolicy)
class VariancePolicyAdmin(admin.ModelAdmin):
    list_display  = ('tier1_ceiling_pct', 'updated_by', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')


# CFO 2026-07-06 claims-PO port
@admin.register(ClaimsAssessment)
class ClaimsAssessmentAdmin(admin.ModelAdmin):
    list_display  = ('claim_number', 'policy_number', 'client_name',
                     'registration', 'company', 'status', 'created_by',
                     'created_at')
    list_filter   = ('status', 'company')
    search_fields = ('claim_number', 'policy_number', 'assessment_number',
                     'client_name', 'registration')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BillAIVerification)
class BillAIVerificationAdmin(admin.ModelAdmin):
    list_display  = ('bill', 'po', 'verdict', 'confidence',
                     'vendor_match', 'total_match', 'currency_match',
                     'elapsed_seconds', 'created_at')
    list_filter   = ('verdict', 'vendor_match', 'total_match', 'currency_match')
    search_fields = ('bill__invoice_number', 'po__po_number')
    readonly_fields = ('raw_prompt', 'raw_response', 'created_at',
                       'elapsed_seconds', 'model_used')
