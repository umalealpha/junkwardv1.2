from django.contrib import admin

from .models import (
    MetricSourceMap,
    SourceFigure,
    ReconciliationRun,
    ReconciliationLine,
    AgeingTieOut,
)


@admin.register(MetricSourceMap)
class MetricSourceMapAdmin(admin.ModelAdmin):
    list_display = ('metric_key', 'label', 'unit', 'flow_type',
                    'include_all_receivable', 'source_system', 'is_active', 'sort_order')
    list_filter = ('unit', 'flow_type', 'source_system', 'is_active')
    search_fields = ('metric_key', 'label')
    filter_horizontal = ('accounts',)


@admin.register(SourceFigure)
class SourceFigureAdmin(admin.ModelAdmin):
    list_display = ('metric_key', 'company', 'period_label', 'source_system',
                    'source_value', 'row_count', 'captured_at')
    list_filter = ('source_system', 'metric_key', 'company')
    search_fields = ('period_label', 'metric_key', 'source_ref')


class ReconciliationLineInline(admin.TabularInline):
    model = ReconciliationLine
    extra = 0
    can_delete = False


class AgeingTieOutInline(admin.TabularInline):
    model = AgeingTieOut
    extra = 0
    can_delete = False


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ('company', 'period_label', 'period_end', 'status',
                    'tolerance_pct', 'run_at', 'run_by')
    list_filter = ('status', 'company')
    search_fields = ('period_label',)
    inlines = [ReconciliationLineInline, AgeingTieOutInline]
