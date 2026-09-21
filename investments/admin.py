from django.contrib import admin

from .models import Investment, InvestmentTransaction


@admin.register(Investment)
class InvestmentAdmin(admin.ModelAdmin):
    list_display = (
        'investment_number', 'name', 'instrument_type',
        'classification', 'face_value', 'cost', 'current_fair_value', 'status',
    )
    list_filter = ('classification', 'instrument_type', 'status')
    search_fields = ('investment_number', 'name', 'isin_or_ref', 'issuer')
    readonly_fields = ('investment_number',)


@admin.register(InvestmentTransaction)
class InvestmentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'transaction_number', 'transaction_date', 'investment',
        'transaction_type', 'amount', 'status',
    )
    list_filter = ('transaction_type', 'status', 'transaction_date')
    search_fields = ('transaction_number',)
    readonly_fields = ('transaction_number', 'journal_entry')
