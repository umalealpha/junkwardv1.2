"""Supplier Payables Reconciliation — DRF serializers.

Money is serialized as a string (DRF's DecimalField default) so the browser
never turns a Pula figure into a float.
"""

from rest_framework import serializers

from .models import (
    Escalation,
    InvoiceReconItem,
    ReasonCode,
    ReconActionLog,
    ReconSupplierProfile,
    SupplierReconLine,
    SupplierReconRun,
)


class ReasonCodeSerializer(serializers.ModelSerializer):
    group_display = serializers.CharField(source='get_group_display', read_only=True)

    class Meta:
        model = ReasonCode
        fields = ['id', 'code', 'label', 'group', 'group_display',
                  'requires_escalation', 'active']


class ReconSupplierProfileSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='contact.name', read_only=True)
    category_display = serializers.CharField(source='get_category_display',
                                             read_only=True)
    payment_terms_days = serializers.IntegerField(
        source='contact.payment_terms_days', read_only=True)

    class Meta:
        model = ReconSupplierProfile
        fields = ['id', 'contact', 'supplier_name', 'category',
                  'category_display', 'in_scope', 'payment_terms_days', 'notes']


class EscalationSerializer(serializers.ModelSerializer):
    raised_by_name = serializers.SerializerMethodField()
    raised_to_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Escalation
        fields = ['id', 'justification', 'amount', 'status', 'status_display',
                  'raised_by_name', 'raised_to_name', 'created_at',
                  'resolved_at', 'resolution_note']

    def get_raised_by_name(self, obj):
        u = obj.raised_by
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()

    def get_raised_to_name(self, obj):
        u = obj.raised_to
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()


class ReconActionLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = ReconActionLog
        fields = ['id', 'action', 'from_status', 'to_status', 'note',
                  'actor_name', 'created_at']

    def get_actor_name(self, obj):
        u = obj.actor
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()


class InvoiceReconItemSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source='invoice.invoice_number',
                                           read_only=True)
    issue_date = serializers.DateField(source='invoice.issue_date', read_only=True)
    # Transaction currency is exposed so a non-BWP bill is never read as Pula.
    currency = serializers.CharField(source='invoice.currency_code_id',
                                     read_only=True)
    face_amount = serializers.DecimalField(source='invoice.total_amount',
                                           max_digits=18, decimal_places=2,
                                           read_only=True)
    supplier_name = serializers.CharField(source='line.supplier.name',
                                          read_only=True)
    category = serializers.CharField(source='line.category', read_only=True)
    po_number = serializers.CharField(source='purchase_order.po_number',
                                      read_only=True, default='')
    grn_number = serializers.CharField(source='goods_receipt.grn_number',
                                       read_only=True, default='')
    reason_code_label = serializers.CharField(source='reason_code.label',
                                              read_only=True, default='')
    match_status_display = serializers.CharField(source='get_match_status_display',
                                                 read_only=True)
    ledger_stage_display = serializers.CharField(source='get_ledger_stage_display',
                                                 read_only=True)
    is_posted = serializers.BooleanField(read_only=True)
    payment_status_display = serializers.CharField(
        source='get_payment_status_display', read_only=True)
    actioned_by_name = serializers.SerializerMethodField()
    actioned_by_department = serializers.SerializerMethodField()

    amount_outstanding = serializers.DecimalField(max_digits=18, decimal_places=2,
                                                  read_only=True)
    days_past_due = serializers.IntegerField(read_only=True)
    ageing_bucket = serializers.CharField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    due_state = serializers.CharField(read_only=True)
    requires_escalation = serializers.BooleanField(read_only=True)
    needs_justification = serializers.BooleanField(read_only=True)
    escalations = EscalationSerializer(many=True, read_only=True)

    class Meta:
        model = InvoiceReconItem
        fields = [
            'id', 'invoice', 'invoice_number', 'issue_date', 'due_date',
            'currency', 'face_amount', 'supplier_name', 'category',
            'amount', 'amount_paid', 'amount_outstanding',
            'payment_status', 'payment_status_display',
            'match_status', 'match_status_display',
            'ledger_stage', 'ledger_stage_display', 'is_posted',
            'po_number', 'grn_number', 'claim_reference',
            'reason_code', 'reason_code_label', 'justification',
            'actioned', 'actioned_by_name', 'actioned_by_department', 'actioned_at',
            'days_past_due', 'ageing_bucket', 'is_overdue', 'due_state',
            'requires_escalation', 'needs_justification', 'escalations',
        ]
        read_only_fields = fields

    def get_actioned_by_name(self, obj):
        u = obj.actioned_by
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()

    def get_actioned_by_department(self, obj):
        # System-stamped attribution (redesign S5): the recorder's department is
        # read from their OMNI profile, never typed on the bill card. Blank when
        # unset — the UI simply omits it.
        u = obj.actioned_by
        if not u:
            return ''
        prof = getattr(u, 'profile', None)
        return ((getattr(prof, 'department', '') or '').strip() if prof else '')


class InvoiceReconItemDetailSerializer(InvoiceReconItemSerializer):
    action_logs = ReconActionLogSerializer(many=True, read_only=True)

    class Meta(InvoiceReconItemSerializer.Meta):
        fields = InvoiceReconItemSerializer.Meta.fields + ['action_logs']
        read_only_fields = fields


class SupplierReconLineSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    category_display = serializers.CharField(source='get_category_display',
                                             read_only=True)
    status_display = serializers.CharField(source='get_status_display',
                                           read_only=True)
    assigned_to_name = serializers.SerializerMethodField()

    class Meta:
        model = SupplierReconLine
        fields = ['id', 'run', 'supplier', 'supplier_name', 'category',
                  'category_display', 'status', 'status_display',
                  'invoiced', 'paid', 'unpaid', 'held',
                  'overdue', 'max_days_past_due',
                  'invoice_count', 'unactioned_count', 'assigned_to',
                  'assigned_to_name']
        read_only_fields = [f for f in fields if f != 'assigned_to']

    def get_assigned_to_name(self, obj):
        u = obj.assigned_to
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()


class SupplierReconRunSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    company_code = serializers.CharField(source='company.code', read_only=True)
    status_display = serializers.CharField(source='get_status_display',
                                           read_only=True)
    prepared_by_name = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()
    is_locked = serializers.BooleanField(read_only=True)
    pct_paid = serializers.FloatField(read_only=True)

    class Meta:
        model = SupplierReconRun
        fields = ['id', 'company', 'company_name', 'company_code',
                  'period_label', 'period_start', 'period_end',
                  'status', 'status_display', 'is_locked',
                  'total_invoiced', 'total_paid', 'total_unpaid', 'total_held',
                  'total_escalated', 'total_not_posted', 'pct_paid',
                  'prepared_by_name', 'reviewed_by_name', 'owner_name',
                  'finalised_at', 'last_built_at', 'created_at']
        read_only_fields = fields

    def _name(self, u):
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()

    def get_prepared_by_name(self, obj):
        return self._name(obj.prepared_by)

    def get_reviewed_by_name(self, obj):
        return self._name(obj.reviewed_by)

    def get_owner_name(self, obj):
        return self._name(obj.owner)
