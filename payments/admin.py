"""
payments/admin.py
"""

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.contrib import messages

from .models import Payment, PaymentAllocation, WithholdingTaxRecord


# ---------------------------------------------------------------------------
# Payment Allocation inline
# ---------------------------------------------------------------------------

class PaymentAllocationInline(admin.TabularInline):
    model  = PaymentAllocation
    extra  = 1
    fields = ('invoice', 'amount_allocated')

    def has_change_permission(self, request, obj=None):
        if obj and obj.status in (
            Payment.Status.CONFIRMED,
            Payment.Status.RECONCILED,
            Payment.Status.CANCELLED,
        ):
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in (
            Payment.Status.CONFIRMED,
            Payment.Status.RECONCILED,
        ):
            return False
        return True


# ---------------------------------------------------------------------------
# Confirm payments — admin action
# ---------------------------------------------------------------------------

@admin.action(description='Confirm selected draft payments')
def confirm_payments(modeladmin, request, queryset):
    confirmed = 0
    for payment in queryset.filter(status=Payment.Status.DRAFT):
        try:
            payment.confirm(user=request.user)
            confirmed += 1
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            modeladmin.message_user(
                request,
                f"{payment.payment_number}: {msg}",
                level=messages.ERROR,
            )
    if confirmed:
        modeladmin.message_user(
            request, f"{confirmed} payment(s) confirmed successfully."
        )


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display    = ('payment_number', 'payment_type', 'contact', 'payment_date',
                       'amount', 'currency_code', 'payment_method', 'status',
                       'journal_entry')
    list_filter     = ('status', 'payment_type', 'payment_method', 'currency_code',
                       'payment_date')
    search_fields   = ('payment_number', 'contact__name', 'reference', 'description')
    readonly_fields = ('id', 'payment_number', 'amount_bwp', 'journal_entry',
                       'created_at', 'updated_at')
    inlines         = (PaymentAllocationInline,)
    actions         = (confirm_payments,)
    date_hierarchy  = 'payment_date'

    fieldsets = (
        ('Header', {
            'fields': ('payment_number', 'payment_type', 'contact',
                       'payment_date', 'payment_method', 'reference'),
        }),
        ('Bank & Currency', {
            'fields': ('bank_account', 'currency_code', 'exchange_rate',
                       'amount', 'amount_bwp'),
        }),
        ('Status', {
            'fields': ('status', 'journal_entry'),
        }),
        ('Notes', {
            'fields': ('description', 'created_by'),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.status in (
            Payment.Status.CONFIRMED,
            Payment.Status.RECONCILED,
            Payment.Status.CANCELLED,
        ):
            base += ['payment_type', 'contact', 'payment_date', 'payment_method',
                     'reference', 'bank_account', 'currency_code', 'exchange_rate',
                     'amount', 'description', 'created_by']
        return base


# ---------------------------------------------------------------------------
# Payment Allocation (standalone)
# ---------------------------------------------------------------------------

@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    list_display    = ('payment', 'invoice', 'amount_allocated', 'created_at')
    list_filter     = ('payment__status', 'invoice__status')
    search_fields   = ('payment__payment_number', 'invoice__invoice_number')
    readonly_fields = ('id', 'created_at', 'updated_at')


# ---------------------------------------------------------------------------
# Withholding Tax Record
# ---------------------------------------------------------------------------

@admin.register(WithholdingTaxRecord)
class WithholdingTaxRecordAdmin(admin.ModelAdmin):
    list_display    = ('payment', 'contact', 'gross_amount', 'wht_rate',
                       'wht_amount', 'net_amount', 'tax_year_start',
                       'cumulative_paid_ytd', 'remitted_to_burs')
    list_filter     = ('remitted_to_burs', 'tax_year_start', 'contact')
    search_fields   = ('payment__payment_number', 'contact__name', 'burs_reference')
    readonly_fields = ('id', 'payment', 'contact', 'gross_amount', 'wht_rate',
                       'wht_amount', 'net_amount', 'tax_year_start',
                       'cumulative_paid_ytd', 'created_at', 'updated_at')
    fieldsets = (
        ('Payment details', {
            'fields': ('payment', 'contact', 'gross_amount', 'wht_rate',
                       'wht_amount', 'net_amount'),
        }),
        ('Tax year', {
            'fields': ('tax_year_start', 'cumulative_paid_ytd'),
        }),
        ('BURS remittance', {
            'fields': ('remitted_to_burs', 'remittance_date', 'burs_reference'),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )
