"""staff_loans/serializers.py — DRF serializers for the staff-loan workflow."""

from __future__ import annotations

from rest_framework import serializers

from . import policy, services
from .models import StaffLoanApplication


class StaffLoanApplicationSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    loan_type_display = serializers.CharField(source='get_loan_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    blue_book_holder_display = serializers.CharField(source='get_blue_book_holder_display', read_only=True)
    cfo_decided_by_name = serializers.SerializerMethodField()
    disbursed_by_name = serializers.SerializerMethodField()

    interest_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_repayable = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    monthly_instalment = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True, allow_null=True)
    employee_loan_id = serializers.UUIDField(source='employee_loan.pk', read_only=True, default=None)
    journal_entry_number = serializers.CharField(source='issuance_journal_entry.entry_number', read_only=True, default=None)

    # permission flags — computed against request.user (single source of truth: the service)
    is_mine = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()
    can_cfo_decide = serializers.SerializerMethodField()
    can_sign = serializers.SerializerMethodField()
    can_disburse = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    bank_accounts = serializers.SerializerMethodField()

    class Meta:
        model = StaffLoanApplication
        fields = [
            'id', 'employee', 'employee_name', 'created_by',
            'loan_type', 'loan_type_display',
            'amount_requested', 'term_months_requested', 'reason',
            'monthly_salary_snapshot',
            'vehicle_description', 'vehicle_reg',
            'no_other_loans_declared', 'purchased_via_veritas',
            'blue_book_holder', 'blue_book_holder_display',
            'blue_book_received', 'blue_book_returned', 'blue_book_returned_at',
            'status', 'status_display', 'submitted_at',
            'cfo_decided_by_name', 'cfo_decided_at', 'decision_notes', 'decline_reason',
            'approved_amount', 'approved_term_months', 'annual_rate_pct',
            'interest_amount', 'total_repayable', 'monthly_instalment', 'outstanding',
            'signatory_full_name', 'signed_at',
            'disbursed_by_name', 'disbursed_at', 'disbursement_ref', 'disbursement_bank_code',
            'employee_loan_id', 'journal_entry_number',
            'created_at', 'is_mine',
            'can_submit', 'can_cfo_decide', 'can_sign', 'can_disburse', 'can_cancel',
            'bank_accounts',
        ]

    def _user(self):
        return getattr(self.context.get('request'), 'user', None)

    def get_is_mine(self, obj):
        u = self._user()
        return bool(u and obj.employee.user_id and obj.employee.user_id == u.pk)

    def get_cfo_decided_by_name(self, obj):
        u = obj.cfo_decided_by
        return (u.get_full_name() or u.username) if u else None

    def get_disbursed_by_name(self, obj):
        u = obj.disbursed_by
        return (u.get_full_name() or u.username) if u else None

    def get_can_submit(self, obj):
        u = self._user()
        return bool(u and obj.status == StaffLoanApplication.Status.DRAFT and services._is_applicant(obj, u))

    def get_can_cfo_decide(self, obj):
        u = self._user()
        return bool(u and obj.status == StaffLoanApplication.Status.PENDING_CFO
                    and services._is_final_approver(u) and not services._is_applicant(obj, u))

    def get_can_sign(self, obj):
        u = self._user()
        return bool(u and obj.status == StaffLoanApplication.Status.APPROVED and services._is_applicant(obj, u))

    def get_can_disburse(self, obj):
        u = self._user()
        # Finance, not HR (CFO 15-Sep-2026). This drives the button, so leaving
        # it on HR showed the action to the one role that can no longer use it.
        return bool(u and obj.status == StaffLoanApplication.Status.SIGNED
                    and services._is_finance_approver(u)
                    and not services._is_applicant(obj, u))

    def get_can_cancel(self, obj):
        u = self._user()
        if not u or obj.status in (StaffLoanApplication.Status.ACTIVE,
                                   StaffLoanApplication.Status.DECLINED,
                                   StaffLoanApplication.Status.CANCELLED):
            return False
        return bool(services._is_applicant(obj, u) or getattr(u, 'is_superuser', False))

    def get_bank_accounts(self, obj):
        # Only needed by whoever is about to disburse.
        if not self.get_can_disburse(obj):
            return []
        return services.company_bank_accounts(obj.employee.company)


class CreateSerializer(serializers.Serializer):
    loan_type = serializers.ChoiceField(choices=StaffLoanApplication.LoanType.choices)
    amount_requested = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=0)
    term_months_requested = serializers.IntegerField(min_value=policy.MIN_TERM_MONTHS)
    reason = serializers.CharField(max_length=4000)
    vehicle_description = serializers.CharField(required=False, allow_blank=True, max_length=200)
    vehicle_reg = serializers.CharField(required=False, allow_blank=True, max_length=40)
    blue_book_holder = serializers.ChoiceField(
        choices=policy.BLUE_BOOK_HOLDERS, required=False, allow_blank=True)
    no_other_loans = serializers.BooleanField(required=False, default=False)
    purchased_via_veritas = serializers.BooleanField(required=False, default=False)


class CfoDecideSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    decline_reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    approved_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    approved_term_months = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    annual_rate_pct = serializers.DecimalField(max_digits=6, decimal_places=3, required=False, allow_null=True)


class SignSerializer(serializers.Serializer):
    signature_data_url = serializers.CharField()
    signatory_full_name = serializers.CharField(max_length=200)


class DisburseSerializer(serializers.Serializer):
    disbursement_bank_code = serializers.CharField(max_length=30)
    disbursement_ref = serializers.CharField(required=False, allow_blank=True, max_length=80)
    blue_book_received = serializers.BooleanField(required=False, default=False)
