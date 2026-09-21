"""
assets/serializers.py

DRF serializers for the Fixed Assets module.
"""

from rest_framework import serializers

from payroll.models import Employee

from .models import (
    Asset,
    AssetAssignment,
    AssetCategory,
    AssetDisposal,
    AssetImportBatch,
    AssetSignOff,
    DepreciationEntry,
)


# ---------------------------------------------------------------------------
# Asset Category
# ---------------------------------------------------------------------------

class AssetCategorySerializer(serializers.ModelSerializer):
    cost_account_code              = serializers.CharField(source='cost_account.code', read_only=True)
    accum_depr_account_code        = serializers.CharField(source='accum_depr_account.code', read_only=True)
    depreciation_expense_account_code = serializers.CharField(
        source='depreciation_expense_account.code', read_only=True,
    )
    method_display                 = serializers.CharField(source='get_default_method_display', read_only=True)

    class Meta:
        model  = AssetCategory
        fields = [
            'id', 'code', 'name',
            'cost_account', 'cost_account_code',
            'accum_depr_account', 'accum_depr_account_code',
            'depreciation_expense_account', 'depreciation_expense_account_code',
            'default_method', 'method_display',
            'default_useful_life_months', 'default_salvage_pct',
            'is_passenger_vehicle',
            'default_capital_allowance_method', 'default_capital_allowance_rate',
            'default_tax_cost_cap',
            'is_active',
            'created_at', 'updated_at',
        ]


# ---------------------------------------------------------------------------
# Depreciation Entry
# ---------------------------------------------------------------------------

class DepreciationEntrySerializer(serializers.ModelSerializer):
    asset_tag      = serializers.CharField(source='asset.tag_number', read_only=True)
    asset_name     = serializers.CharField(source='asset.name', read_only=True)
    period_name    = serializers.CharField(source='period.period_name', read_only=True)
    journal_entry_number = serializers.CharField(source='journal_entry.entry_number', read_only=True, default=None)

    class Meta:
        model  = DepreciationEntry
        fields = [
            'id', 'asset', 'asset_tag', 'asset_name',
            'period', 'period_name', 'period_end_date',
            'amount', 'journal_entry', 'journal_entry_number',
            'reversed_at', 'created_at',
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Asset Disposal
# ---------------------------------------------------------------------------

class AssetDisposalSerializer(serializers.ModelSerializer):
    asset_tag       = serializers.CharField(source='asset.tag_number', read_only=True)
    asset_name      = serializers.CharField(source='asset.name', read_only=True)
    disposal_type_display = serializers.CharField(source='get_disposal_type_display', read_only=True)
    journal_entry_number = serializers.CharField(source='journal_entry.entry_number', read_only=True, default=None)

    class Meta:
        model  = AssetDisposal
        fields = [
            'id', 'asset', 'asset_tag', 'asset_name',
            'disposal_type', 'disposal_type_display',
            'disposal_date', 'proceeds', 'bank_account',
            'cost_at_disposal', 'accumulated_depr_at_disposal',
            'nbv_at_disposal', 'gain_loss',
            'journal_entry', 'journal_entry_number',
            'notes', 'created_at',
        ]
        read_only_fields = [
            'cost_at_disposal', 'accumulated_depr_at_disposal',
            'nbv_at_disposal', 'gain_loss', 'journal_entry',
            'journal_entry_number', 'created_at',
        ]


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

class AssetListSerializer(serializers.ModelSerializer):
    category_code           = serializers.CharField(source='category.code', read_only=True)
    category_name           = serializers.CharField(source='category.name', read_only=True)
    company_code            = serializers.CharField(source='company.code', read_only=True)
    status_display          = serializers.CharField(source='get_status_display', read_only=True)
    custody_status_display  = serializers.CharField(source='get_custody_status_display', read_only=True)
    condition_display       = serializers.CharField(source='get_condition_display', read_only=True)
    method_display          = serializers.CharField(source='get_method_display', read_only=True)
    vat_treatment_display   = serializers.CharField(source='get_vat_treatment_display', read_only=True)
    accumulated_depreciation = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    net_book_value          = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    tax_cost                = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    cost_capped_by_tax_rule = serializers.BooleanField(read_only=True)
    custodian_employee_name = serializers.SerializerMethodField()

    def get_custodian_employee_name(self, obj):
        return obj.custodian_employee.full_name if obj.custodian_employee_id else None

    class Meta:
        model  = Asset
        fields = [
            'id', 'tag_number', 'external_ref', 'name',
            'company', 'company_code',
            'category', 'category_code', 'category_name',
            'cost', 'salvage_value',
            'accumulated_depreciation', 'net_book_value',
            'method', 'method_display',
            'useful_life_months',
            'purchase_date', 'in_service_date', 'last_depreciation_date',
            'location', 'custodian',
            'custodian_employee', 'custodian_employee_name',
            'status', 'status_display',
            'condition', 'condition_display',
            'custody_status', 'custody_status_display',
            'vat_treatment', 'vat_treatment_display', 'purchase_vat_amount',
            'capital_allowance_method', 'capital_allowance_rate',
            'tax_cost_cap', 'tax_cost', 'cost_capped_by_tax_rule',
            'created_at', 'updated_at',
        ]


class AssetAssignmentSerializer(serializers.ModelSerializer):
    """Read-only hand-over history row."""
    transferred_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = AssetAssignment
        fields = [
            'id', 'from_custodian', 'to_custodian', 'from_location', 'to_location',
            'reason', 'transferred_at', 'transferred_by', 'transferred_by_name',
            'created_at',
        ]

    def get_transferred_by_name(self, obj):
        u = obj.transferred_by
        return (u.get_full_name() or u.username) if u else None


class AssetTransferActionSerializer(serializers.Serializer):
    """Input for POST /assets/{id}/transfer/ — hand an asset to a new holder/location."""
    to_employee    = serializers.PrimaryKeyRelatedField(
                         queryset=Employee.objects.all(), required=False, allow_null=True)
    to_location    = serializers.CharField(required=False, allow_blank=True, max_length=200)
    reason         = serializers.CharField(required=False, allow_blank=True, max_length=300)
    transferred_at = serializers.DateField(required=False)

    def validate(self, data):
        if not data.get('to_employee') and not (data.get('to_location') or '').strip():
            raise serializers.ValidationError('Choose a new holder and/or a new location.')
        return data


class AssetDetailSerializer(AssetListSerializer):
    depreciation_entries = DepreciationEntrySerializer(many=True, read_only=True)
    disposal             = AssetDisposalSerializer(read_only=True)
    assignments          = AssetAssignmentSerializer(many=True, read_only=True)

    class Meta(AssetListSerializer.Meta):
        fields = AssetListSerializer.Meta.fields + [
            'description', 'serial_number', 'barcode',
            'opening_accumulated_depreciation',
            'notes', 'depreciation_entries', 'disposal', 'assignments',
        ]


class AssetCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Asset
        fields = [
            # Same defect the New PO page had, found in the same sweep
            # (2026-09-10): api_views.get_serializer_class() returns THIS
            # serializer for `create`, and frontend assets/new/page.tsx does
            # router.push(`/assets/${created.id}`). Without 'id' on the 201
            # every newly created asset lands the user on /assets/undefined.
            'id',
            'tag_number', 'external_ref', 'name', 'description',
            'serial_number', 'barcode',
            'company', 'category',
            'cost', 'salvage_value',
            'method', 'useful_life_months',
            'purchase_date', 'in_service_date',
            'opening_accumulated_depreciation', 'last_depreciation_date',
            'location', 'custodian', 'custodian_employee', 'notes',
            # Tax / VAT side
            'vat_treatment', 'purchase_vat_amount',
            'capital_allowance_method', 'capital_allowance_rate', 'tax_cost_cap',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        purchase = attrs.get('purchase_date')
        in_service = attrs.get('in_service_date') or purchase
        attrs['in_service_date'] = in_service
        # If category is a passenger vehicle and the user didn't override the
        # tax-side fields, pull defaults from the category. Saves the user from
        # repeating BURS rules every time.
        category = attrs.get('category')
        if category is not None:
            if attrs.get('tax_cost_cap') in (None, ''):
                attrs['tax_cost_cap'] = category.default_tax_cost_cap
            if not attrs.get('capital_allowance_method'):
                attrs['capital_allowance_method'] = category.default_capital_allowance_method
            if attrs.get('capital_allowance_rate') in (None, ''):
                attrs['capital_allowance_rate'] = category.default_capital_allowance_rate
            # Default VAT treatment for passenger vehicles is denied
            if category.is_passenger_vehicle and not attrs.get('vat_treatment'):
                attrs['vat_treatment'] = Asset.VatTreatment.DENIED
        return super().validate(attrs)


# ---------------------------------------------------------------------------
# Disposal action payload
# ---------------------------------------------------------------------------

class AssetDisposalActionSerializer(serializers.Serializer):
    disposal_type = serializers.ChoiceField(choices=AssetDisposal.DisposalType.choices)
    disposal_date = serializers.DateField()
    proceeds      = serializers.DecimalField(max_digits=18, decimal_places=2, default=0)
    bank_account  = serializers.UUIDField(required=False, allow_null=True)
    notes         = serializers.CharField(required=False, allow_blank=True, default='')


# ---------------------------------------------------------------------------
# Import batch
# ---------------------------------------------------------------------------

class AssetImportBatchSerializer(serializers.ModelSerializer):
    company_code  = serializers.CharField(source='company.code', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    created_by_username       = serializers.CharField(source='created_by.username', read_only=True, default=None)
    first_approver_username   = serializers.CharField(source='first_approved_by.username', read_only=True, default=None)
    second_approver_username  = serializers.CharField(source='second_approved_by.username', read_only=True, default=None)
    rejected_by_username      = serializers.CharField(source='rejected_by.username', read_only=True, default=None)
    is_fully_approved         = serializers.BooleanField(read_only=True)

    class Meta:
        model  = AssetImportBatch
        fields = [
            'id', 'source', 'file_name', 'cutover_date',
            'rows_total', 'rows_valid', 'rows_invalid', 'rows_imported',
            'parsed_rows', 'validation_errors',
            'status', 'status_display',
            'company', 'company_code',
            'created_by_username',
            'first_approver_username',  'first_approved_at',
            'second_approver_username', 'second_approved_at',
            'rejected_by_username', 'rejected_at', 'rejection_reason',
            'is_fully_approved',
            'created_at', 'committed_at',
        ]
        read_only_fields = [
            'rows_total', 'rows_valid', 'rows_invalid', 'rows_imported',
            'parsed_rows', 'validation_errors',
            'status', 'status_display', 'company_code',
            'created_by_username', 'first_approver_username', 'first_approved_at',
            'second_approver_username', 'second_approved_at',
            'rejected_by_username', 'rejected_at', 'rejection_reason',
            'is_fully_approved', 'created_at', 'committed_at',
        ]


class AssetImportPreviewSerializer(serializers.Serializer):
    file         = serializers.FileField()
    cutover_date = serializers.DateField()
    company      = serializers.UUIDField()
    source       = serializers.CharField(default='odoo', required=False)


# ---------------------------------------------------------------------------
# Run monthly depreciation payload
# ---------------------------------------------------------------------------

class RunDepreciationSerializer(serializers.Serializer):
    period_name = serializers.CharField(help_text='e.g. 2026-04')
    company     = serializers.UUIDField(required=False, allow_null=True)
    dry_run     = serializers.BooleanField(default=False)


# ---------------------------------------------------------------------------
# Asset Sign-Off
# ---------------------------------------------------------------------------

class AssetSignOffSerializer(serializers.ModelSerializer):
    kind_display      = serializers.CharField(source='get_kind_display', read_only=True)
    status_display    = serializers.CharField(source='get_status_display', read_only=True)
    asset_tag         = serializers.CharField(source='asset.tag_number', read_only=True, default=None)
    asset_name        = serializers.CharField(source='asset.name', read_only=True, default=None)
    first_signed_by_username  = serializers.CharField(source='first_signed_by.username', read_only=True, default=None)
    second_signed_by_username = serializers.CharField(source='second_signed_by.username', read_only=True, default=None)
    is_complete       = serializers.BooleanField(read_only=True)
    is_overdue        = serializers.BooleanField(read_only=True)

    class Meta:
        model  = AssetSignOff
        fields = [
            'id', 'kind', 'kind_display',
            'asset', 'asset_tag', 'asset_name',
            'period_label', 'due_date', 'company',
            'first_signed_by', 'first_signed_by_username', 'first_signed_at', 'first_role_label',
            'second_signed_by', 'second_signed_by_username', 'second_signed_at', 'second_role_label',
            'status', 'status_display',
            'counted_assets', 'discrepancies_text', 'notes',
            'is_complete', 'is_overdue',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'kind_display', 'status_display', 'asset_tag', 'asset_name',
            'first_signed_by', 'first_signed_by_username', 'first_signed_at',
            'second_signed_by', 'second_signed_by_username', 'second_signed_at',
            'is_complete', 'is_overdue', 'created_at', 'updated_at',
        ]


class AssetSignOffActionSerializer(serializers.Serializer):
    """Payload for /sign/ — optional notes + counted_assets + discrepancies."""
    counted_assets     = serializers.IntegerField(required=False, min_value=0)
    discrepancies_text = serializers.CharField(required=False, allow_blank=True)
    notes              = serializers.CharField(required=False, allow_blank=True)
