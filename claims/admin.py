from django.contrib import admin

from .models import (
    ClaimForm, RecoveryImportBatch, Salvage, Subrogation,
    SubrogationPanel, SubrogationReceipt, SubrogationGLConfig,
)


@admin.register(SubrogationGLConfig)
class SubrogationGLConfigAdmin(admin.ModelAdmin):
    list_display = ('recovery_income_account', 'updated_by', 'updated_at')


@admin.register(ClaimForm)
class ClaimFormAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'slug', 'active', 'sort_order', 'updated_at')
    list_filter  = ('category', 'active')
    search_fields = ('title', 'slug', 'source_name')
    ordering = ('sort_order', 'title')


class _ReceiptInline(admin.TabularInline):
    model = SubrogationReceipt
    extra = 0
    fields = ('received_date', 'amount', 'method', 'reference', 'realpay_txn_id')


@admin.register(Subrogation)
class SubrogationAdmin(admin.ModelAdmin):
    list_display  = ('claim_reference', 'third_party_name', 'claim_type',
                     'appointed_to', 'date_appointed', 'status')
    list_filter   = ('status', 'claim_type', 'company', 'appointed_to')
    search_fields = ('claim_reference', 'third_party_name', 'third_party_insurer')
    date_hierarchy = 'created_at'
    inlines       = [_ReceiptInline]


@admin.register(Salvage)
class SalvageAdmin(admin.ModelAdmin):
    list_display  = ('claim_reference', 'asset_description', 'estimated_value',
                     'sale_proceeds', 'sale_date', 'status')
    list_filter   = ('status', 'company')
    search_fields = ('claim_reference', 'asset_description', 'buyer_name')
    date_hierarchy = 'created_at'


@admin.register(SubrogationPanel)
class SubrogationPanelAdmin(admin.ModelAdmin):
    list_display  = ('name', 'kind', 'contact_name', 'contact_phone', 'is_active')
    list_filter   = ('kind', 'is_active')
    search_fields = ('name', 'contact_name', 'contact_email')
    ordering      = ('name',)


@admin.register(SubrogationReceipt)
class SubrogationReceiptAdmin(admin.ModelAdmin):
    list_display  = ('subrogation', 'received_date', 'amount', 'method', 'reference')
    list_filter   = ('method',)
    search_fields = ('subrogation__claim_reference', 'reference', 'realpay_txn_id')
    date_hierarchy = 'received_date'


@admin.register(RecoveryImportBatch)
class RecoveryImportBatchAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'kind', 'status', 'rows_total',
                    'rows_imported', 'rows_skipped_dup', 'created_at')
    list_filter  = ('status', 'kind')
    readonly_fields = ('parsed_rows', 'validation_errors', 'created_at', 'committed_at')
