from django.contrib import admin

from .models import Exception as ExceptionModel


@admin.register(ExceptionModel)
class ExceptionAdmin(admin.ModelAdmin):
    list_display  = ('title', 'exception_type', 'severity', 'status',
                     'requires_role', 'source_label', 'created_at')
    list_filter   = ('status', 'severity', 'exception_type', 'requires_role')
    search_fields = ('title', 'description', 'source_label')
    readonly_fields = ('created_at', 'updated_at',
                       'acknowledged_at', 'resolved_at', 'dismissed_at',
                       'linker_notified_at', 'linker_response')
