from django.contrib import admin

from .models import (
    ForexPaymentHistory, ForexPaymentImport, PlannedForexPayment,
    RecurringForexPayee,
)


@admin.register(ForexPaymentImport)
class ForexPaymentImportAdmin(admin.ModelAdmin):
    list_display = ('filename', 'imported_count', 'row_count', 'date_from',
                    'date_to', 'created_at')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ForexPaymentHistory)
class ForexPaymentHistoryAdmin(admin.ModelAdmin):
    list_display = ('reference', 'beneficiary', 'currency', 'amount',
                    'value_date', 'status')
    list_filter = ('currency', 'status')
    search_fields = ('reference', 'beneficiary', 'beneficiary_key')


@admin.register(RecurringForexPayee)
class RecurringForexPayeeAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'currency', 'typical_amount', 'cadence',
                    'months_active', 'confidence', 'active')
    list_filter = ('currency', 'cadence', 'active')
    search_fields = ('display_name', 'beneficiary_key')


@admin.register(PlannedForexPayment)
class PlannedForexPaymentAdmin(admin.ModelAdmin):
    list_display = ('expected_value_date', 'currency', 'expected_amount',
                    'beneficiary', 'driver', 'status', 'estimated_bwp')
    list_filter = ('currency', 'driver', 'status')
    search_fields = ('beneficiary', 'beneficiary_key')
