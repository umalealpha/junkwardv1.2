from django.contrib import admin

from watchdog.models import WatchdogFinding, WatchdogReportLog, WatchdogRun


class FindingInline(admin.TabularInline):
    model = WatchdogFinding
    extra = 0
    fields = ("severity", "category", "module", "title", "flagged", "auto_action",
              "resolved")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(WatchdogRun)
class WatchdogRunAdmin(admin.ModelAdmin):
    list_display = ("run_date", "focus", "status", "findings_danger",
                    "findings_safe", "checks_passed", "checks_run", "dry_run")
    list_filter = ("status", "dry_run", "run_date")
    date_hierarchy = "run_date"
    inlines = [FindingInline]


@admin.register(WatchdogFinding)
class WatchdogFindingAdmin(admin.ModelAdmin):
    list_display = ("run", "severity", "category", "module", "title", "flagged",
                    "resolved")
    list_filter = ("severity", "category", "flagged", "resolved")
    search_fields = ("check_key", "title", "detail")


@admin.register(WatchdogReportLog)
class WatchdogReportLogAdmin(admin.ModelAdmin):
    list_display = ("report_date", "created_at")
    date_hierarchy = "report_date"
