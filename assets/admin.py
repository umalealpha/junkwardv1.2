"""
assets/admin.py
"""

from django.contrib import admin

from .models import (
    Asset,
    AssetCategory,
    AssetControlPolicy,
    AssetDisposal,
    AssetHandover,
    AssetImportBatch,
    AssetRequisition,
    AssetSignOff,
    DepreciationEntry,
)


@admin.register(AssetSignOff)
class AssetSignOffAdmin(admin.ModelAdmin):
    list_display = ('kind', 'period_label', 'asset', 'due_date', 'status',
                    'first_signed_by', 'second_signed_by')
    list_filter  = ('kind', 'status')
    search_fields = ('period_label', 'asset__tag_number', 'asset__name', 'notes')
    date_hierarchy = 'due_date'


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'default_method', 'default_useful_life_months', 'is_active')
    list_filter   = ('default_method', 'is_active')
    search_fields = ('code', 'name')


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        'tag_number', 'name', 'category', 'company',
        'cost', 'salvage_value', 'status',
        'purchase_date', 'last_depreciation_date',
    )
    list_filter   = ('status', 'method', 'category', 'company')
    search_fields = ('tag_number', 'external_ref', 'name', 'serial_number')
    date_hierarchy = 'purchase_date'
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(DepreciationEntry)
class DepreciationEntryAdmin(admin.ModelAdmin):
    list_display = ('asset', 'period', 'period_end_date', 'amount', 'reversed_at')
    list_filter  = ('period',)
    search_fields = ('asset__tag_number', 'asset__name')
    date_hierarchy = 'period_end_date'


@admin.register(AssetDisposal)
class AssetDisposalAdmin(admin.ModelAdmin):
    list_display = ('asset', 'disposal_type', 'disposal_date', 'proceeds', 'gain_loss')
    list_filter  = ('disposal_type',)
    date_hierarchy = 'disposal_date'


@admin.register(AssetImportBatch)
class AssetImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        'file_name', 'source', 'status',
        'rows_total', 'rows_valid', 'rows_invalid', 'rows_imported',
        'cutover_date', 'created_at', 'committed_at',
    )
    list_filter  = ('status', 'source')
    readonly_fields = ('parsed_rows', 'validation_errors', 'created_at', 'committed_at')


# ---------------------------------------------------------------------------
# Asset Control & Handover
# ---------------------------------------------------------------------------

@admin.register(AssetRequisition)
class AssetRequisitionAdmin(admin.ModelAdmin):
    list_display = ('requisition_number', 'req_type', 'recipient_name', 'category',
                    'estimated_value', 'status', 'requires_full_gate', 'created_at')
    list_filter  = ('status', 'req_type', 'requires_full_gate')
    search_fields = ('requisition_number', 'recipient_name', 'recipient_email', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AssetHandover)
class AssetHandoverAdmin(admin.ModelAdmin):
    list_display = ('handover_number', 'asset', 'recipient_name', 'status',
                    'it_released_by', 'finance_recorded_by', 'employee_accepted_by')
    list_filter  = ('status',)
    search_fields = ('handover_number', 'recipient_name', 'asset__tag_number')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AssetControlPolicy)
class AssetControlPolicyAdmin(admin.ModelAdmin):
    list_display = ('material_threshold_bwp', 'is_active', 'updated_at')
