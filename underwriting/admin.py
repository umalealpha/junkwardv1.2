from django.contrib import admin

from .models import GraphiteMapping, QuoteRateFloor, QuoteTemplate


@admin.register(QuoteRateFloor)
class QuoteRateFloorAdmin(admin.ModelAdmin):
    """The lowest premium rate % a quotation may use. Leave class_of_business
    blank for the default that applies to every class; add a row per class to
    override it. A floor of 0 (or inactive) means no check."""
    list_display = ('class_of_business', 'min_rate_pct', 'min_premium', 'active', 'note', 'updated_at')
    list_editable = ('min_rate_pct', 'min_premium', 'active')
    search_fields = ('class_of_business',)


@admin.register(QuoteTemplate)
class QuoteTemplateAdmin(admin.ModelAdmin):
    """A reusable starting point for a quote: the standard cover rows for a
    class of business, plus a default rate. Picking it on the quote screen fills
    the class, the cover table and the rate — all still editable."""
    list_display = ('name', 'class_of_business', 'rate_pct', 'active', 'updated_at')
    list_editable = ('active',)
    search_fields = ('name', 'class_of_business')


@admin.register(GraphiteMapping)
class GraphiteMappingAdmin(admin.ModelAdmin):
    """Class of business -> Graphite product/plan, and broker -> agent code. Set
    these when TheRiskCo issues the codes; an unmapped value stops a conversion
    rather than creating a policy on the wrong product."""
    list_display = ('kind', 'omni_value', 'product_id', 'plan_id', 'agent_code', 'active')
    list_editable = ('product_id', 'plan_id', 'agent_code', 'active')
    list_filter = ('kind', 'active')
    search_fields = ('omni_value', 'agent_code')
