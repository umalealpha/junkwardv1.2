from django.contrib import admin

from .models import (
    PettyCashLocation,
    PettyCashReimbursement,
    PettyCashVoucher,
)


@admin.register(PettyCashLocation)
class PettyCashLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'float_amount', 'custodian', 'is_active')
    list_filter = ('is_active', 'company')
    search_fields = ('name', 'address')


@admin.register(PettyCashVoucher)
class PettyCashVoucherAdmin(admin.ModelAdmin):
    list_display = (
        'voucher_number', 'voucher_date', 'location', 'payee',
        'amount', 'status',
    )
    list_filter = ('status', 'location', 'voucher_date')
    search_fields = ('voucher_number', 'payee', 'description')
    readonly_fields = ('voucher_number', 'journal_entry', 'reimbursement')

    # Once a voucher is posted/reimbursed its GL entry exists — editing the
    # amount/account/status here would desync the tin from the ledger. Lock
    # the whole record (the model also blocks it, but the UI shouldn't offer).
    _LOCKED = (PettyCashVoucher.Status.POSTED, PettyCashVoucher.Status.REIMBURSED)

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj is not None and obj.status in self._LOCKED:
            return [f.name for f in obj._meta.fields]
        return base

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status in self._LOCKED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(PettyCashReimbursement)
class PettyCashReimbursementAdmin(admin.ModelAdmin):
    list_display = (
        'reimbursement_number', 'reimbursement_date', 'location',
        'total_amount', 'voucher_count', 'status',
    )
    list_filter = ('status', 'location', 'reimbursement_date')
    search_fields = ('reimbursement_number',)
    readonly_fields = ('reimbursement_number', 'journal_entry')
