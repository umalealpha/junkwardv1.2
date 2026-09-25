"""integrations/admin.py"""
from django.contrib import admin
from django.utils import timezone

from .models import (
    GraphitePaymentSyncRun,
    GraphitePaymentTransaction,
    IntegrationEvent,
    OutboundEvent,
    TimeDoctorUserMap,
)


@admin.register(IntegrationEvent)
class IntegrationEventAdmin(admin.ModelAdmin):
    list_display  = ['received_at', 'source_system', 'event_type', 'status',
                     'result_type', 'retry_count']
    list_filter   = ['source_system', 'event_type', 'status']
    search_fields = ['event_type', 'error_message']
    readonly_fields = ['id', 'received_at', 'processed_at', 'result_type',
                       'result_id', 'retry_count', 'error_message']
    ordering = ['-received_at']


@admin.register(OutboundEvent)
class OutboundEventAdmin(admin.ModelAdmin):
    """WS1 outbound state bus — the write-back outbox. Operators watch failures
    here and can force a retry."""
    list_display  = ['created_at', 'target', 'event_type', 'status', 'attempts',
                     'response_status', 'next_attempt_at', 'idempotency_key']
    list_filter   = ['target', 'status', 'event_type']
    search_fields = ['event_type', 'idempotency_key', 'last_error', 'endpoint']
    readonly_fields = ['id', 'created_at', 'updated_at', 'sent_at', 'attempts',
                       'response_status', 'last_error', 'next_attempt_at']
    ordering = ['-created_at']
    actions = ['retry_now']

    @admin.action(description='Retry now (re-deliver selected events)')
    def retry_now(self, request, queryset):
        from .outbound import deliver
        sent = 0
        for ev in queryset:
            ev.status = OutboundEvent.Status.PENDING
            ev.attempts = 0          # a manual retry is a clean fresh start
            ev.last_error = ''
            ev.next_attempt_at = timezone.now()
            ev.save(update_fields=['status', 'attempts', 'last_error',
                                   'next_attempt_at', 'updated_at'])
            if deliver(ev).get('sent'):
                sent += 1
        self.message_user(request, f'{queryset.count()} re-attempted, {sent} sent.')


@admin.register(GraphitePaymentSyncRun)
class GraphitePaymentSyncRunAdmin(admin.ModelAdmin):
    list_display  = ['created_at', 'window_from', 'window_to', 'partner',
                     'status', 'rows_seen', 'rows_created', 'rows_updated',
                     'dry_run']
    list_filter   = ['status', 'dry_run', 'partner']
    readonly_fields = ['id', 'created_at', 'updated_at', 'started_at',
                       'finished_at']
    ordering = ['-created_at']


@admin.register(TimeDoctorUserMap)
class TimeDoctorUserMapAdmin(admin.ModelAdmin):
    """Confirm TD ↔ payroll links here once; unmatched queue = employee empty."""
    list_display  = ['td_name', 'td_email', 'employee', 'confirmed', 'source', 'updated_at']
    list_filter   = ['confirmed', 'source']
    search_fields = ['td_name', 'td_email', 'td_user_id', 'employee__full_name']
    autocomplete_fields = ['employee']
    readonly_fields = ['id', 'td_user_id', 'created_at', 'updated_at']
    ordering = ['td_name']

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        if change:
            obj.source = TimeDoctorUserMap.Source.MANUAL
        super().save_model(request, obj, form, change)


@admin.register(GraphitePaymentTransaction)
class GraphitePaymentTransactionAdmin(admin.ModelAdmin):
    list_display  = ['graphite_id', 'source_recorded_at', 'payment_method',
                     'amount', 'status', 'policy_number', 'is_refund']
    list_filter   = ['payment_method', 'status_norm', 'is_refund', 'is_reverse']
    search_fields = ['policy_number', 'reference_number', 'dpo_trans_id',
                     'graphite_id']
    readonly_fields = ['id', 'created_at', 'updated_at', 'synced_at', 'raw']
    ordering = ['-source_recorded_at']
