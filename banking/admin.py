"""
banking/admin.py
"""

from django.contrib import admin

from .models import (
    BankAccount, BankMatchMemory, BankStatement, BankStatementFormat,
    BankStatementLine,
)


# ---------------------------------------------------------------------------
# BankAccount
# ---------------------------------------------------------------------------

@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display    = ('bank_name', 'account_name', 'account_number',
                       'gl_account', 'currency_code', 'current_balance',
                       'last_reconciled_date', 'is_active')
    list_filter     = ('bank_name', 'is_active', 'currency_code')
    search_fields   = ('bank_name', 'account_name', 'account_number')
    readonly_fields = ('id', 'current_balance', 'last_reconciled_date',
                       'created_at', 'updated_at')

    fieldsets = (
        ('Bank details', {
            'fields': ('gl_account', 'bank_name', 'account_name',
                       'account_number', 'branch_code', 'currency_code'),
        }),
        ('Status', {
            'fields': ('is_active', 'current_balance', 'last_reconciled_date'),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )


# ---------------------------------------------------------------------------
# BankStatementFormat
# ---------------------------------------------------------------------------

@admin.register(BankStatementFormat)
class BankStatementFormatAdmin(admin.ModelAdmin):
    list_display    = ('name', 'bank_name', 'delimiter', 'encoding',
                       'date_format', 'sign_convention')
    list_filter     = ('bank_name',)
    search_fields   = ('name', 'bank_name')
    readonly_fields = ('id', 'created_at', 'updated_at')

    fieldsets = (
        ('Identity', {
            'fields': ('name', 'bank_name'),
        }),
        ('File settings', {
            'fields': ('delimiter', 'encoding', 'skip_rows'),
        }),
        ('Column mapping', {
            'fields': ('date_column', 'date_format', 'description_column',
                       'reference_column', 'balance_column'),
        }),
        ('Amount columns', {
            'fields': ('amount_column', 'sign_convention',
                       'debit_column', 'credit_column'),
            'description': (
                'Use either amount_column (with sign_convention) '
                'OR debit_column + credit_column.'
            ),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )


# ---------------------------------------------------------------------------
# BankStatementLine inline
# ---------------------------------------------------------------------------

class BankStatementLineInline(admin.TabularInline):
    model   = BankStatementLine
    extra   = 0
    fields  = ('line_number', 'transaction_date', 'description', 'reference',
               'amount', 'running_balance', 'match_status',
               'matched_payment', 'match_confidence')
    readonly_fields = ('line_number', 'transaction_date', 'description',
                       'reference', 'amount', 'running_balance',
                       'match_status', 'matched_payment', 'match_confidence')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# BankStatement
# ---------------------------------------------------------------------------

@admin.register(BankStatement)
class BankStatementAdmin(admin.ModelAdmin):
    list_display    = ('statement_number', 'bank_account', 'statement_date',
                       'opening_balance', 'closing_balance', 'line_count',
                       'status', 'import_date')
    list_filter     = ('status', 'bank_account', 'statement_date')
    search_fields   = ('statement_number', 'bank_account__account_name', 'file_name')
    readonly_fields = ('id', 'statement_number', 'import_date',
                       'line_count', 'created_at', 'updated_at')
    inlines         = (BankStatementLineInline,)

    fieldsets = (
        ('Header', {
            'fields': ('statement_number', 'bank_account', 'statement_date',
                       'opening_balance', 'closing_balance'),
        }),
        ('Import', {
            'fields': ('file_name', 'import_date', 'imported_by', 'line_count'),
        }),
        ('Status', {
            'fields': ('status',),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )


# ---------------------------------------------------------------------------
# BankStatementLine (standalone)
# ---------------------------------------------------------------------------

@admin.register(BankStatementLine)
class BankStatementLineAdmin(admin.ModelAdmin):
    list_display    = ('statement', 'line_number', 'transaction_date',
                       'description', 'reference', 'amount', 'match_status',
                       'matched_payment', 'match_confidence')
    list_filter     = ('match_status', 'statement__bank_account',
                       'transaction_date')
    search_fields   = ('description', 'reference',
                       'statement__statement_number',
                       'matched_payment__payment_number')
    readonly_fields = ('id', 'raw_data', 'created_at', 'updated_at')

    fieldsets = (
        ('Transaction', {
            'fields': ('statement', 'line_number', 'transaction_date',
                       'description', 'reference', 'amount', 'running_balance'),
        }),
        ('Matching', {
            'fields': ('match_status', 'matched_payment',
                       'matched_journal_entry', 'match_confidence', 'notes'),
        }),
        ('Debug', {
            'classes': ('collapse',),
            'fields': ('raw_data',),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )


@admin.register(BankMatchMemory)
class BankMatchMemoryAdmin(admin.ModelAdmin):
    """L-BANKAI: what the match suggestions have learned. Suggest-only — a
    finance user can switch a memory off; nothing here ever matches a line."""
    list_display  = ('company', 'counterparty_key', 'payee_key', 'times_confirmed',
                     'last_confirmed_at', 'disabled')
    list_editable = ('disabled',)
    list_filter   = ('company', 'disabled')
    search_fields = ('counterparty_key', 'payee_key')
