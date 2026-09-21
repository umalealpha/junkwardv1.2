"""
core/admin.py — Admin registration for all core models.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import UserProfile, AuditLog, Currency, ExchangeRate, TaxRate


# ---------------------------------------------------------------------------
# Inline: UserProfile inside the User admin
# ---------------------------------------------------------------------------

class UserProfileInline(admin.StackedInline):
    model          = UserProfile
    can_delete     = False
    verbose_name_plural = 'Profile'
    fields         = ('role', 'department', 'is_active')


class UserAdmin(BaseUserAdmin):
    inlines      = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'get_role', 'is_staff', 'is_active')
    list_filter  = BaseUserAdmin.list_filter + ('profile__role',)

    @admin.display(description='Role')
    def get_role(self, obj):
        try:
            return obj.profile.get_role_display()
        except UserProfile.DoesNotExist:
            return '—'


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display    = ('user', 'role', 'department', 'is_active', 'created_at')
    list_filter     = ('role', 'is_active')
    search_fields   = ('user__username', 'user__email', 'department')
    readonly_fields = ('id', 'created_at', 'updated_at')


# ---------------------------------------------------------------------------
# AuditLog  — read-only; no add / change / delete permissions
# ---------------------------------------------------------------------------

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display    = ('created_at', 'action', 'table_name', 'record_id', 'user', 'ip_address')
    list_filter     = ('action', 'table_name', 'created_at')
    search_fields   = ('table_name', 'record_id', 'user__username', 'description')
    readonly_fields = (
        'id', 'table_name', 'record_id', 'action',
        'old_values', 'new_values', 'user', 'ip_address', 'description', 'created_at',
    )
    date_hierarchy  = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'symbol', 'decimal_places', 'is_active')
    list_filter   = ('is_active',)
    search_fields = ('code', 'name')


# ---------------------------------------------------------------------------
# ExchangeRate
# ---------------------------------------------------------------------------

@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display    = ('from_currency', 'to_currency', 'rate', 'effective_date', 'source', 'created_at')
    list_filter     = ('source', 'to_currency', 'effective_date')
    search_fields   = ('from_currency__code', 'to_currency__code')
    readonly_fields = ('id', 'created_at', 'updated_at')
    date_hierarchy  = 'effective_date'


# ---------------------------------------------------------------------------
# TaxRate
# ---------------------------------------------------------------------------

@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display    = ('tax_code', 'name', 'rate', 'is_active', 'effective_from', 'effective_to')
    list_filter     = ('is_active',)
    search_fields   = ('tax_code', 'name')
    readonly_fields = ('id', 'created_at', 'updated_at')
