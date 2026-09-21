"""
ledger/admin.py
"""

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.contrib import messages

from .models import Account, FiscalPeriod, FrozenFigure, JournalEntry, JournalEntryLine


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display    = ('code', 'name', 'account_type', 'sub_type', 'currency_code',
                       'is_bank_account', 'parent', 'is_active')
    list_filter     = ('account_type', 'sub_type', 'is_bank_account', 'is_active', 'currency_code')
    search_fields   = ('code', 'name', 'description')
    readonly_fields = ('id', 'created_at', 'updated_at')
    fieldsets       = (
        (None, {
            'fields': ('code', 'name', 'account_type', 'sub_type', 'parent'),
        }),
        ('Settings', {
            'fields': ('currency_code', 'is_bank_account', 'is_active', 'description'),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )


# ---------------------------------------------------------------------------
# Fiscal Period
# ---------------------------------------------------------------------------

@admin.register(FiscalPeriod)
class FiscalPeriodAdmin(admin.ModelAdmin):
    list_display    = ('period_name', 'start_date', 'end_date', 'status', 'closed_by', 'closed_at')
    list_filter     = ('status',)
    search_fields   = ('period_name',)
    readonly_fields = ('id', 'created_at', 'updated_at')


# ---------------------------------------------------------------------------
# Journal Entry Lines (inline)
# ---------------------------------------------------------------------------

class JournalEntryLineInline(admin.TabularInline):
    model  = JournalEntryLine
    extra  = 2
    fields = ('account', 'description', 'debit_amount', 'credit_amount',
              'debit_bwp', 'credit_bwp')

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status in (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED):
            return ('account', 'description', 'debit_amount', 'credit_amount',
                    'debit_bwp', 'credit_bwp')
        return ()

    def has_add_permission(self, request, obj=None):
        if obj and obj.status in (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED):
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED):
            return False
        return True


# ---------------------------------------------------------------------------
# Journal Entry admin action — Post
# ---------------------------------------------------------------------------

@admin.action(description='Post selected draft journal entries')
def post_journal_entries(modeladmin, request, queryset):
    posted = 0
    for entry in queryset.filter(status=JournalEntry.Status.DRAFT):
        try:
            entry.post(user=request.user)
            posted += 1
        except ValidationError as e:
            modeladmin.message_user(
                request,
                f"{entry.entry_number}: {'; '.join(e.messages)}",
                level=messages.ERROR,
            )
    if posted:
        modeladmin.message_user(request, f"{posted} entry/entries posted successfully.")


# ---------------------------------------------------------------------------
# Journal Entry
# ---------------------------------------------------------------------------

@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display    = ('entry_number', 'entry_date', 'journal_type', 'description',
                       'status', 'currency_code', 'created_by', 'posted_date')
    list_filter     = ('status', 'journal_type', 'currency_code', 'entry_date')
    search_fields   = ('entry_number', 'description', 'notes')
    readonly_fields = ('id', 'entry_number', 'posted_date', 'reversed_by',
                       'created_at', 'updated_at')
    inlines         = (JournalEntryLineInline,)
    actions         = (post_journal_entries,)
    date_hierarchy  = 'entry_date'

    fieldsets = (
        ('Header', {
            'fields': ('entry_number', 'entry_date', 'journal_type', 'description',
                       'source_type', 'source_id'),
        }),
        ('Currency', {
            'fields': ('currency_code', 'exchange_rate'),
        }),
        ('Status', {
            'fields': ('status', 'posted_date', 'reversed_by', 'reversal_of'),
        }),
        ('People', {
            'fields': ('created_by', 'approved_by'),
        }),
        ('Notes', {
            'fields': ('notes',),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.status in (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED):
            base += ['entry_date', 'journal_type', 'description', 'source_type',
                     'source_id', 'currency_code', 'exchange_rate', 'reversal_of',
                     'created_by', 'approved_by', 'notes']
        return base


# ---------------------------------------------------------------------------
# Journal Entry Line (standalone — for audit searching across all lines)
# ---------------------------------------------------------------------------

@admin.register(JournalEntryLine)
class JournalEntryLineAdmin(admin.ModelAdmin):
    list_display    = ('journal_entry', 'account', 'debit_amount', 'credit_amount',
                       'debit_bwp', 'credit_bwp', 'description')
    list_filter     = ('account__account_type', 'journal_entry__status')
    search_fields   = ('journal_entry__entry_number', 'account__code',
                       'account__name', 'description')
    readonly_fields = ('id', 'created_at', 'updated_at')

    def has_change_permission(self, request, obj=None):
        if obj and obj.journal_entry.status in (
            JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED
        ):
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        if obj and obj.journal_entry.status in (
            JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED
        ):
            return False
        return True


# ---------------------------------------------------------------------------
# FrozenFigure — CFO-locked audit headline values
# ---------------------------------------------------------------------------

@admin.register(FrozenFigure)
class FrozenFigureAdmin(admin.ModelAdmin):
    list_display       = (
        'period', 'line_label', 'value_bwp', 'tolerance_pct',
        'is_active', 'acknowledged_by_override', 'locked_at',
    )
    list_filter        = ('period', 'is_active', 'acknowledged_by_override')
    search_fields      = ('period', 'line_label', 'notes')
    readonly_fields    = (
        'id', 'created_at', 'updated_at', 'locked_at',
        'acknowledged_by', 'acknowledged_at',
    )
    fieldsets = (
        (None, {
            'fields': (
                'period', 'line_label', 'value_bwp', 'tolerance_pct',
                'is_active', 'notes',
            ),
        }),
        ('Lock metadata', {
            'fields': ('locked_by', 'locked_at'),
        }),
        ('Override acknowledgement', {
            'fields': (
                'acknowledged_by_override',
                'acknowledged_by',
                'acknowledged_at',
            ),
        }),
        ('System', {'fields': ('id', 'created_at', 'updated_at')}),
    )
