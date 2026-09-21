"""
billing/admin.py
"""

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.contrib import messages

from .models import Contact, Invoice, InvoiceLine


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display    = ('name', 'contact_type', 'email', 'phone', 'currency_code',
                       'payment_terms_days', 'is_active')
    list_filter     = ('contact_type', 'is_active', 'is_resident', 'wht_exempt',
                       'currency_code')
    search_fields   = ('name', 'email', 'phone', 'registration_number', 'tax_id',
                       'graphite_id')
    readonly_fields = ('id', 'created_at', 'updated_at')
    fieldsets       = (
        (None, {
            'fields': ('contact_type', 'name', 'registration_number', 'tax_id'),
        }),
        ('Contact details', {
            'fields': ('email', 'phone', 'address'),
        }),
        ('Finance settings', {
            'fields': ('currency_code', 'payment_terms_days', 'is_resident',
                       'wht_exempt', 'is_active'),
        }),
        ('Integration', {
            'classes': ('collapse',),
            'fields': ('graphite_id',),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )


# ---------------------------------------------------------------------------
# Invoice Lines inline
# ---------------------------------------------------------------------------

class InvoiceLineInline(admin.TabularInline):
    model  = InvoiceLine
    extra  = 1
    fields = ('account', 'description', 'quantity', 'unit_price', 'tax_code',
              'tax_rate', 'tax_amount', 'line_total')

    def get_readonly_fields(self, request, obj=None):
        # Auto-calculated fields are always read-only
        base = ('tax_rate', 'tax_amount', 'line_total')
        if obj and obj.status != Invoice.Status.DRAFT:
            return ('account', 'description', 'quantity', 'unit_price',
                    'tax_code') + base
        return base

    def has_add_permission(self, request, obj=None):
        if obj and obj.status != Invoice.Status.DRAFT:
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status != Invoice.Status.DRAFT:
            return False
        return True


# ---------------------------------------------------------------------------
# Post invoices — admin action
# ---------------------------------------------------------------------------

@admin.action(description='Post selected draft invoices')
def post_invoices(modeladmin, request, queryset):
    posted = 0
    for invoice in queryset.filter(status=Invoice.Status.DRAFT):
        try:
            invoice.post(user=request.user)
            posted += 1
        except (ValidationError, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            modeladmin.message_user(
                request,
                f"{invoice.invoice_number}: {msg}",
                level=messages.ERROR,
            )
    if posted:
        modeladmin.message_user(request, f"{posted} invoice(s) posted successfully.")


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display    = ('invoice_number', 'contact', 'invoice_type', 'issue_date',
                       'due_date', 'total_amount', 'status', 'journal_entry')
    list_filter     = ('status', 'invoice_type', 'issue_date', 'currency_code')
    search_fields   = ('invoice_number', 'contact__name', 'description',
                       'source_id')
    readonly_fields = ('id', 'invoice_number', 'subtotal', 'tax_total',
                       'total_amount', 'balance_due', 'journal_entry',
                       'created_at', 'updated_at')
    inlines         = (InvoiceLineInline,)
    actions         = (post_invoices,)
    date_hierarchy  = 'issue_date'

    fieldsets = (
        ('Header', {
            'fields': ('invoice_number', 'invoice_type', 'contact',
                       'issue_date', 'due_date'),
        }),
        ('Currency', {
            'fields': ('currency_code', 'exchange_rate'),
        }),
        ('Totals', {
            'fields': ('subtotal', 'tax_total', 'total_amount',
                       'amount_paid', 'balance_due'),
        }),
        ('Status', {
            'fields': ('status', 'journal_entry'),
        }),
        ('Source / Notes', {
            'fields': ('source_type', 'source_id', 'description', 'created_by'),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('id', 'created_at', 'updated_at'),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.status != Invoice.Status.DRAFT:
            base += ['invoice_type', 'contact', 'issue_date', 'due_date',
                     'currency_code', 'exchange_rate', 'amount_paid',
                     'source_type', 'source_id', 'description', 'created_by']
        return base


# ---------------------------------------------------------------------------
# Invoice Line (standalone — for searching across all lines)
# ---------------------------------------------------------------------------

@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display    = ('invoice', 'account', 'description', 'quantity',
                       'unit_price', 'tax_rate', 'tax_amount', 'line_total')
    list_filter     = ('account__account_type', 'tax_code')
    search_fields   = ('invoice__invoice_number', 'account__code',
                       'account__name', 'description')
    readonly_fields = ('id', 'tax_rate', 'tax_amount', 'line_total',
                       'created_at', 'updated_at')
