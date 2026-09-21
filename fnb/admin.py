from django.contrib import admin

from .models import FNBBatchSubmission, FNBSyncLog, FNBWebhookEvent


@admin.register(FNBSyncLog)
class FNBSyncLogAdmin(admin.ModelAdmin):
    list_display  = ('created_at', 'direction', 'service', 'status',
                     'http_status', 'endpoint', 'elapsed_ms')
    list_filter   = ('direction', 'service', 'status')
    search_fields = ('endpoint', 'request_summary', 'error_message')
    readonly_fields = ('created_at', 'updated_at',
                       'request_payload', 'response_payload')


@admin.register(FNBBatchSubmission)
class FNBBatchSubmissionAdmin(admin.ModelAdmin):
    list_display  = ('idempotency_key', 'source_account', 'payment_count',
                     'total_amount_bwp', 'status', 'submitted_at')
    list_filter   = ('status', 'currency_code')
    search_fields = ('idempotency_key', 'fnb_reference')
    readonly_fields = ('created_at', 'updated_at',
                       'payload_snapshot',
                       'submitted_at', 'acknowledged_at', 'settled_at')


@admin.register(FNBWebhookEvent)
class FNBWebhookEventAdmin(admin.ModelAdmin):
    list_display  = ('received_at', 'event_type', 'external_id',
                     'status', 'processed_at')
    list_filter   = ('status',)
    search_fields = ('event_type', 'external_id')
    readonly_fields = ('received_at', 'processed_at',
                       'raw_payload', 'raw_headers', 'signature')
