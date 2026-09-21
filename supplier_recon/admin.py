"""Django admin for supplier reconciliation.

Bulk-classifying suppliers is the one job that is genuinely faster in admin
than in the app, so ReconSupplierProfile is fully editable here. Runs, lines
and bill items are read-only: a reconciliation must be changed through the
workflow (which records who, when and why), never by typing over it.
"""

from django.contrib import admin

from .models import (
    Escalation,
    ReconOwner,
    InvoiceReconItem,
    ReasonCode,
    ReconActionLog,
    ReconSupplierProfile,
    SupplierReconLine,
    SupplierReconRun,
)


@admin.register(ReconSupplierProfile)
class ReconSupplierProfileAdmin(admin.ModelAdmin):
    list_display = ('contact', 'category', 'in_scope')
    list_filter = ('category', 'in_scope')
    list_editable = ('category', 'in_scope')
    search_fields = ('contact__name',)
    autocomplete_fields = ('contact',)
    ordering = ('contact__name',)


@admin.register(ReconOwner)
class ReconOwnerAdmin(admin.ModelAdmin):
    """Who owns each entity's board. Escalations default to this person."""
    list_display = ('company', 'owner')
    autocomplete_fields = ('owner',)
    search_fields = ('company__code', 'company__name', 'owner__username')


@admin.register(ReasonCode)
class ReasonCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'label', 'group', 'requires_escalation', 'active')
    list_filter = ('group', 'requires_escalation', 'active')
    list_editable = ('requires_escalation', 'active')
    search_fields = ('code', 'label')


class SupplierReconLineInline(admin.TabularInline):
    model = SupplierReconLine
    extra = 0
    can_delete = False
    fields = ('supplier', 'category', 'status', 'invoiced', 'paid', 'unpaid',
              'held', 'invoice_count', 'unactioned_count')
    readonly_fields = fields


@admin.register(SupplierReconRun)
class SupplierReconRunAdmin(admin.ModelAdmin):
    list_display = ('period_label', 'company', 'status', 'total_invoiced',
                    'total_paid', 'total_unpaid', 'total_held', 'finalised_at')
    list_filter = ('status', 'company', 'period_label')
    search_fields = ('period_label', 'company__code', 'company__name')
    inlines = [SupplierReconLineInline]
    readonly_fields = [f.name for f in SupplierReconRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(InvoiceReconItem)
class InvoiceReconItemAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'line', 'payment_status', 'match_status',
                    'amount', 'amount_paid', 'due_date', 'actioned')
    list_filter = ('payment_status', 'match_status', 'actioned')
    search_fields = ('invoice__invoice_number', 'line__supplier__name',
                     'claim_reference')
    readonly_fields = [f.name for f in InvoiceReconItem._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Escalation)
class EscalationAdmin(admin.ModelAdmin):
    list_display = ('item', 'status', 'amount', 'raised_by', 'created_at',
                    'resolved_at')
    list_filter = ('status',)
    search_fields = ('item__invoice__invoice_number', 'justification')
    readonly_fields = [f.name for f in Escalation._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ReconActionLog)
class ReconActionLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'item', 'action', 'from_status', 'to_status',
                    'actor')
    list_filter = ('action',)
    search_fields = ('item__invoice__invoice_number', 'note')
    readonly_fields = [f.name for f in ReconActionLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
