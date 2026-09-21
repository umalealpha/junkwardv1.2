"""genric/admin.py — where Finance changes the GENRIC pack's settings.

The only reason this file exists: the pay window before cancellation is a
business decision, not a deployment detail. It has to be changeable on screen by
the people who own it, and the change has to be visible and audited. Registering
the row here is what makes "no deploy needed" true rather than merely intended —
a setting model with no admin is an environment variable with extra steps.

Mirrors payroll.admin.PayrollSettingAdmin.
"""
from django.contrib import admin

from .models import GenricSetting


@admin.register(GenricSetting)
class GenricSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'value', 'description')
    list_editable = ('value',)
    search_fields = ('key', 'description')
    ordering = ('key',)

    # The key is the contract between this row and the code that reads it.
    # Renaming one on screen would not fail — it would silently create an
    # unread row while the real key re-seeds itself back to its default, and
    # Finance would be adjusting a number nothing consults.
    readonly_fields = ('key',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
