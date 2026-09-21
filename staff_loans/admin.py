from django.contrib import admin

from .models import StaffLoanApplication, StaffLoanRate


@admin.register(StaffLoanRate)
class StaffLoanRateAdmin(admin.ModelAdmin):
    list_display = ('effective_from', 'annual_rate_pct', 'reference_rate_pct',
                    'spread_pct', 'source', 'created_at')
    list_filter = ('source',)
    readonly_fields = ('created_at', 'updated_at')
    # CFO/Finance may add a MANUAL row here to override the scheme rate, or
    # adjust the spread the monthly cron applies over the BoB MoPR.


@admin.register(StaffLoanApplication)
class StaffLoanApplicationAdmin(admin.ModelAdmin):
    list_display = ('employee', 'loan_type', 'amount_requested', 'status',
                    'approved_amount', 'annual_rate_pct', 'disbursed_at')
    list_filter = ('loan_type', 'status', 'blue_book_holder')
    search_fields = ('employee__full_name', 'vehicle_reg', 'disbursement_ref')
    # Money + workflow fields are set only through the approval/disbursement
    # flow (which enforces caps, SoD and GL posting). Editing them here would
    # bypass all of that, so they are read-only in the admin.
    readonly_fields = ('created_at', 'updated_at', 'submitted_at', 'cfo_decided_at',
                       'signed_at', 'disbursed_at', 'employee_loan', 'issuance_journal_entry',
                       'status', 'amount_requested', 'term_months_requested',
                       'approved_amount', 'approved_term_months', 'annual_rate_pct',
                       'monthly_salary_snapshot', 'blue_book_received',
                       'signature_data_url', 'signed_ip', 'disbursement_ref')
    autocomplete_fields = ('employee',)
