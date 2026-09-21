from django.contrib import admin

from .models import M365ActiveUser, M365LicenseSyncRun


@admin.register(M365ActiveUser)
class M365ActiveUserAdmin(admin.ModelAdmin):
    list_display = ("display_name", "email", "department", "job_title",
                    "license_count", "last_interactive_signin_at", "refreshed_at")
    search_fields = ("display_name", "email", "user_principal_name", "department")
    list_filter = ("department",)
    readonly_fields = [f.name for f in M365ActiveUser._meta.fields]


@admin.register(M365LicenseSyncRun)
class M365LicenseSyncRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "finished_at", "success", "active_count",
                    "inserted", "updated", "removed", "triggered_by")
    list_filter = ("success", "triggered_by")
    readonly_fields = [f.name for f in M365LicenseSyncRun._meta.fields]
