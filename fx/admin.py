from django.contrib import admin

from .models import FXRevaluation, FXRevaluationLine


class FXRevaluationLineInline(admin.TabularInline):
    model = FXRevaluationLine
    extra = 0


@admin.register(FXRevaluation)
class FXRevaluationAdmin(admin.ModelAdmin):
    list_display  = ('period', 'company', 'run_date', 'status',
                     'total_gain_bwp', 'total_loss_bwp', 'net_bwp')
    list_filter   = ('status',)
    search_fields = ('period__period_name',)
    inlines       = [FXRevaluationLineInline]
