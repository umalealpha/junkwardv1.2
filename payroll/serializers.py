"""payroll/serializers.py"""
from rest_framework import serializers

from .models import (
    Employee, PayrollImportBatch, PayrollPeriod, Payslip, PayslipComponent,
    PayslipLine, TaxBracket,
)


class EmployeeSerializer(serializers.ModelSerializer):
    company_code = serializers.CharField(source='company.code', read_only=True, default=None)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    archived_by_name = serializers.CharField(source='archived_by.get_full_name', read_only=True, default=None)
    retention_expiry_date = serializers.DateField(read_only=True)

    class Meta:
        model  = Employee
        fields = [
            'id', 'employee_number', 'full_name', 'department', 'job_title',
            'email', 'phone', 'hire_date', 'termination_date',
            'company', 'company_code',
            'status', 'status_display',
            'bank_name', 'bank_account_no', 'bank_branch',
            'external_ref',
            # PAY-008 housing benefit (BURS § 32)
            'housing_benefit_type', 'housing_rateable_value',
            'housing_floor_area_m2', 'housing_furniture_cost',
            'salary_sacrifice_housing',
            # Keep access after exit (CFO 2026-09-18) — the contractor tick the
            # automatic leaver close-off honours. Writable: HR sets it.
            'keep_access_after_exit',
            # Terminated Employee Archive (Oprah Mogomotsi feature request, 2026-08-13)
            'is_archived', 'archived_at', 'archived_by_name', 'archive_reason',
            'retention_years', 'retention_expiry_date',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at',
            # Archiving/unarchiving goes through the dedicated actions
            # (payroll/api_views.py archive/unarchive) so it is always
            # reason- and audit-logged — never a plain field PATCH.
            'is_archived', 'archived_at', 'archived_by_name', 'archive_reason', 'retention_expiry_date',
        ]

    def to_representation(self, instance):
        # DPA L-1 (2026-07-19): never emit the full bank account over the API —
        # mask to last-4. Payment/bank files read the model directly (not this
        # serializer), so masking the API output loses nothing operationally.
        data = super().to_representation(instance)
        acc = data.get('bank_account_no')
        # Full number for authorised payroll/HR in the DETAIL view (bank confirmation
        # letters, payments — CFO 2026-07-20); masked to last-4 everywhere else (list).
        if acc and not self.context.get('full_bank'):
            digits = ''.join(ch for ch in str(acc) if ch.isdigit()) or str(acc)
            data['bank_account_no'] = ('••••' + digits[-4:]) if len(digits) >= 4 else '••••'
        return data

    def _drop_masked_account(self, validated_data):
        # Guard: never write a masked placeholder back. If the UI round-trips the
        # masked read value on save it would corrupt the real number; a genuine
        # new number carries no bullet char and is written normally.
        if '•' in str(validated_data.get('bank_account_no') or ''):
            validated_data.pop('bank_account_no', None)
        return validated_data

    def create(self, validated_data):
        return super().create(self._drop_masked_account(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._drop_masked_account(validated_data))


class TaxBracketSerializer(serializers.ModelSerializer):
    class Meta:
        model  = TaxBracket
        fields = [
            'id', 'name', 'effective_from',
            'lower_bound', 'upper_bound',
            'base_amount', 'rate_pct', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PayslipComponentSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    # Bug c369818a (Oprah, /hris/payroll-setup): the page resolved the GL account
    # NAME client-side from /accounts/, which is capped at 100 rows
    # (core.pagination.CappedPageNumberPagination), so only the lowest-coded
    # mapped account (107008) fell inside the first page and showed a name.
    # Resolve it server-side instead — immune to pagination, always correct.
    posting_account_name = serializers.SerializerMethodField()

    class Meta:
        model  = PayslipComponent
        fields = [
            'id', 'code', 'name', 'kind', 'kind_display',
            'sort_order', 'is_taxable', 'is_active',
            'posting_account_code', 'posting_account_name',
            # Without this the /hris/payroll-setup screen cannot set it, and
            # the model comment claiming the CFO can mark a component one-off
            # "without a deploy" is false — the next one-off he adds would
            # default to recurring and roll forward, re-opening the duplicate
            # pay defect this field exists to close.
            'is_recurring',
        ]

    def get_posting_account_name(self, obj):
        code = (obj.posting_account_code or '').strip()
        if not code:
            return ''
        from ledger.models import Account
        acct = (Account.objects.filter(code=code, is_active=True).first()
                or Account.objects.filter(code=code).first())
        return acct.name if acct else ''


class PayrollPeriodSerializer(serializers.ModelSerializer):
    payslip_count   = serializers.IntegerField(source='payslips.count', read_only=True)
    status_display  = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = PayrollPeriod
        fields = ['id', 'period_name', 'start_date', 'end_date', 'pay_date',
                  'status', 'status_display', 'payslip_count', 'notes',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'payslip_count', 'created_at', 'updated_at']


class PayslipLineSerializer(serializers.ModelSerializer):
    component_code = serializers.CharField(source='component.code', read_only=True)
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_kind = serializers.CharField(source='component.kind', read_only=True)

    class Meta:
        model  = PayslipLine
        fields = ['id', 'component', 'component_code', 'component_name', 'component_kind',
                  'amount', 'notes']


class PayslipSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    department    = serializers.CharField(source='employee.department', read_only=True)
    period_name   = serializers.CharField(source='period.period_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    lines         = PayslipLineSerializer(many=True, read_only=True)

    class Meta:
        model  = Payslip
        fields = ['id', 'employee', 'employee_name', 'department',
                  'period', 'period_name',
                  'company', 'gross_amount', 'paye_amount', 'net_amount', 'ctc_amount',
                  'status', 'status_display', 'notes', 'lines',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'gross_amount', 'paye_amount', 'net_amount', 'ctc_amount',
                            'created_at', 'updated_at']


class PayrollImportBatchSerializer(serializers.ModelSerializer):
    status_display          = serializers.CharField(source='get_status_display', read_only=True)
    period_name             = serializers.CharField(source='period.period_name', read_only=True, default=None)
    created_by_username     = serializers.CharField(source='created_by.username', read_only=True, default=None)
    first_approver_username = serializers.CharField(source='first_approved_by.username', read_only=True, default=None)
    second_approver_username = serializers.CharField(source='second_approved_by.username', read_only=True, default=None)
    rejected_by_username    = serializers.CharField(source='rejected_by.username', read_only=True, default=None)
    is_fully_approved       = serializers.BooleanField(read_only=True)

    class Meta:
        model  = PayrollImportBatch
        fields = [
            'id', 'source', 'file_name',
            'period', 'period_name',
            'rows_total', 'rows_valid', 'rows_invalid',
            'rows_skipped_dup', 'rows_imported',
            'parsed_rows', 'validation_errors',
            'status', 'status_display',
            'company', 'created_by_username',
            'first_approver_username', 'first_approved_at',
            'second_approver_username', 'second_approved_at',
            'rejected_by_username', 'rejected_at', 'rejection_reason',
            'is_fully_approved',
            'created_at', 'committed_at',
        ]
        read_only_fields = [f for f in fields if f not in ('source', 'file_name', 'period', 'company')]
