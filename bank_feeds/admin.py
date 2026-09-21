from django.contrib import admin

from .models import BankFeedConfig, BankFeedRun


@admin.register(BankFeedConfig)
class BankFeedConfigAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'bank_account', 'protocol',
        'schedule_cron', 'status', 'last_run_at',
    )
    list_filter = ('protocol', 'status')
    search_fields = ('name', 'env_prefix', 'remote_path')


@admin.register(BankFeedRun)
class BankFeedRunAdmin(admin.ModelAdmin):
    list_display = (
        'config', 'started_at', 'outcome',
        'files_seen', 'statements_created', 'lines_created',
    )
    list_filter = ('outcome',)
    search_fields = ('config__name', 'error_message')
    readonly_fields = (
        'config', 'triggered_by', 'started_at', 'finished_at',
        'outcome', 'files_seen', 'files_processed',
        'statements_created', 'lines_created', 'duplicates_skipped',
        'error_message', 'file_hashes',
    )
