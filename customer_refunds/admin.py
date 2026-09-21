from django.contrib import admin

from .models import CustomerRefund


@admin.register(CustomerRefund)
class CustomerRefundAdmin(admin.ModelAdmin):
    list_display = ('policy_number', 'refund_amount', 'currency', 'status',
                    'account_last4', 'ai_greenlight', 'created_at')
    list_filter = ('status', 'ai_greenlight', 'currency')
    search_fields = ('policy_number', 'graphite_ref', 'customer_name')
    # account_number_enc holds encrypted PII — never expose it in admin.
    readonly_fields = ('account_number_enc', 'account_last4', 'graphite_ref',
                       'created_at', 'updated_at')
