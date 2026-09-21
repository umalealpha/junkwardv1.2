"""
assets/control_serializers.py

DRF serializers for the Asset Control & Handover module.
Read serializers expose display labels + snapshots; write serializers validate
input at the boundary (the controls themselves live in control_services.py).
"""

from rest_framework import serializers

from .control_models import AssetControlPolicy, AssetHandover, AssetRequisition


class AssetRequisitionSerializer(serializers.ModelSerializer):
    req_type_display = serializers.CharField(source='get_req_type_display', read_only=True)
    status_display   = serializers.CharField(source='get_status_display', read_only=True)
    category_name    = serializers.CharField(source='category.name', read_only=True)
    company_code     = serializers.CharField(source='company.code', read_only=True)
    requested_by_name = serializers.CharField(source='requested_by.get_full_name', read_only=True)
    fm_approved_by_name  = serializers.SerializerMethodField()
    cfo_approved_by_name = serializers.SerializerMethodField()
    spare_asset_tag  = serializers.CharField(source='spare_asset.tag_number', read_only=True)
    handover_id      = serializers.SerializerMethodField()

    class Meta:
        model = AssetRequisition
        fields = [
            'id', 'requisition_number', 'req_type', 'req_type_display',
            'company', 'company_code', 'category', 'category_name',
            'description', 'estimated_value', 'spare_asset', 'spare_asset_tag',
            'recipient', 'recipient_name', 'recipient_email', 'reason',
            'status', 'status_display', 'requires_full_gate',
            'requested_by', 'requested_by_name', 'submitted_at',
            'fm_approved_by', 'fm_approved_by_name', 'fm_approved_at', 'fm_comment',
            'cfo_approved_by', 'cfo_approved_by_name', 'cfo_approved_at', 'cfo_comment',
            'rejected_by', 'rejected_at', 'rejection_reason',
            'resulting_asset', 'handover_id',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_fm_approved_by_name(self, obj):
        return obj.fm_approved_by.get_full_name() if obj.fm_approved_by_id else ''

    def get_cfo_approved_by_name(self, obj):
        return obj.cfo_approved_by.get_full_name() if obj.cfo_approved_by_id else ''

    def get_handover_id(self, obj):
        ho = getattr(obj, 'handover', None)
        return str(ho.id) if ho else None


class AssetRequisitionCreateSerializer(serializers.Serializer):
    req_type        = serializers.ChoiceField(choices=AssetRequisition.Type.choices)
    category        = serializers.UUIDField()
    description     = serializers.CharField(max_length=300)
    estimated_value = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=0)
    reason          = serializers.CharField(max_length=500)
    recipient       = serializers.UUIDField(help_text='Employee id — picked from the directory, never typed.')
    spare_asset     = serializers.UUIDField(required=False, allow_null=True)


class RequisitionDecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class RequisitionRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class AssetHandoverSerializer(serializers.ModelSerializer):
    status_display   = serializers.CharField(source='get_status_display', read_only=True)
    asset_tag        = serializers.CharField(source='asset.tag_number', read_only=True)
    asset_name       = serializers.CharField(source='asset.name', read_only=True)
    requisition_number = serializers.CharField(source='requisition.requisition_number', read_only=True)
    it_released_by_name    = serializers.SerializerMethodField()
    finance_recorded_by_name = serializers.SerializerMethodField()
    employee_accepted_by_name = serializers.SerializerMethodField()
    is_complete      = serializers.BooleanField(read_only=True)

    class Meta:
        model = AssetHandover
        fields = [
            'id', 'handover_number', 'requisition', 'requisition_number',
            'asset', 'asset_tag', 'asset_name',
            'recipient', 'recipient_name', 'recipient_email',
            'status', 'status_display', 'condition_on_issue', 'accessories',
            'it_released_by', 'it_released_by_name', 'it_released_at',
            'finance_recorded_by', 'finance_recorded_by_name', 'finance_recorded_at',
            'employee_accepted_by', 'employee_accepted_by_name', 'employee_accepted_at',
            'is_complete', 'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_it_released_by_name(self, obj):
        return obj.it_released_by.get_full_name() if obj.it_released_by_id else ''

    def get_finance_recorded_by_name(self, obj):
        return obj.finance_recorded_by.get_full_name() if obj.finance_recorded_by_id else ''

    def get_employee_accepted_by_name(self, obj):
        return obj.employee_accepted_by.get_full_name() if obj.employee_accepted_by_id else ''


class HandoverCreateSerializer(serializers.Serializer):
    requisition        = serializers.UUIDField()
    asset              = serializers.UUIDField()
    condition_on_issue = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    accessories        = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')


class HandoverSignSerializer(serializers.Serializer):
    signature = serializers.CharField(required=False, allow_blank=True, default='',
                                      help_text='Optional e-signature PNG data-URL.')


class AssetControlPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = AssetControlPolicy
        fields = ['id', 'material_threshold_bwp', 'it_officer_emails', 'is_active', 'updated_at']
        read_only_fields = ['id', 'updated_at']
