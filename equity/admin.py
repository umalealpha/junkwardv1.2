from django.contrib import admin

from .models import EsopGrant, ShareHolding, Stakeholder, VestingTranche


class ShareHoldingInline(admin.TabularInline):
    model = ShareHolding
    extra = 0


class VestingTrancheInline(admin.TabularInline):
    model = VestingTranche
    extra = 0


@admin.register(Stakeholder)
class StakeholderAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'employee', 'is_current')
    list_filter = ('kind', 'is_current')
    search_fields = ('name', 'email')
    inlines = [ShareHoldingInline]


@admin.register(EsopGrant)
class EsopGrantAdmin(admin.ModelAdmin):
    list_display = ('stakeholder', 'units', 'grant_date', 'status')
    list_filter = ('status',)
    search_fields = ('stakeholder__name', 'letter_ref')
    inlines = [VestingTrancheInline]


admin.site.register(ShareHolding)
admin.site.register(VestingTranche)
