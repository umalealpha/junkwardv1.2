"""fnb/serializers.py — DRF serializers for the audit log + batches."""

from rest_framework import serializers

from .models import FNBBatchSubmission, FNBSyncLog, FNBWebhookEvent


class FNBSyncLogSerializer(serializers.ModelSerializer):
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    service_display   = serializers.CharField(source='get_service_display', read_only=True)
    status_display    = serializers.CharField(source='get_status_display', read_only=True)
    triggered_by_username = serializers.CharField(
        source='triggered_by_user.username', read_only=True,
    )

    class Meta:
        model  = FNBSyncLog
        fields = [
            'id',
            'direction', 'direction_display',
            'service', 'service_display',
            'status', 'status_display',
            'endpoint', 'http_method', 'http_status',
            'elapsed_ms',
            'request_summary', 'request_payload', 'response_payload',
            'error_message',
            'triggered_by_user', 'triggered_by_username',
            'created_at',
        ]


class FNBBatchSubmissionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    source_account_name = serializers.CharField(
        source='source_account.account_name', read_only=True,
    )
    submitted_by_username = serializers.CharField(
        source='submitted_by.username', read_only=True,
    )
    # The payee "To" bank account(s) for the batch (CFO 2026-08-30). The data
    # already lives on each linked Payment — either its VendorBankAccount or the
    # inline once-off payee fields — so this only surfaces it, nothing new is
    # stored. Falls back to the stored payload snapshot for older batches whose
    # payments M2M is empty (e.g. Quick-Transfer). Never exposes a balance.
    recipients = serializers.SerializerMethodField()

    @staticmethod
    def _mask(number) -> str:
        """Last four digits only. The batch LIST is a bulk view of every payee
        we bank with; the full number belongs on the single-payment screen,
        where every view is logged (Fable 5.1 audit 2026-09-02, M7)."""
        n = str(number or '').strip()
        return ('*' * (len(n) - 4) + n[-4:]) if len(n) > 4 else n

    def get_recipients(self, obj):
        out = []
        for p in obj.payments.all():
            vba = getattr(p, 'vendor_bank_account', None)
            if vba:
                out.append({
                    'holder': vba.account_holder_name or '',
                    'bank': vba.bank_name or '',
                    'account_number': self._mask(vba.account_number),
                })
            elif (getattr(p, 'payee_account_number', '') or '').strip():
                out.append({
                    'holder': p.payee_name or '',
                    'bank': p.payee_bank_name or '',
                    'account_number': self._mask(p.payee_account_number),
                })
        if not out:
            snap = obj.payload_snapshot or {}
            try:
                for tx in snap.get('creditTransferTransactionInformation', []) or []:
                    ca = tx.get('creditorAccount', {}) or {}
                    if ca.get('accountNumber'):
                        out.append({
                            'holder': (tx.get('creditor', {}) or {}).get('name', '') or '',
                            'bank': '',
                            'account_number': self._mask(ca.get('accountNumber', '')),
                        })
            except (AttributeError, TypeError):
                pass
        return out

    class Meta:
        model  = FNBBatchSubmission
        fields = [
            'id', 'idempotency_key',
            'source_account', 'source_account_name',
            'recipients',
            'payment_count', 'total_amount_bwp', 'currency_code',
            'status', 'status_display',
            'submitted_at', 'acknowledged_at', 'settled_at',
            'fnb_reference', 'failure_reason',
            'submitted_by', 'submitted_by_username',
            'created_at', 'updated_at',
        ]


class FNBWebhookEventSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = FNBWebhookEvent
        fields = [
            'id', 'event_type', 'external_id',
            'received_at', 'processed_at',
            'status', 'status_display',
            'raw_payload', 'raw_headers', 'signature',
            'related_batch', 'error_message',
        ]


class FNBStatusSerializer(serializers.Serializer):
    """Computed connection / readiness status for the /banking/fnb page."""
    configured       = serializers.BooleanField()
    auth_mode        = serializers.CharField()
    api_base         = serializers.CharField()
    last_sync_at     = serializers.DateTimeField(allow_null=True)
    last_sync_status = serializers.CharField()
    pending_batches  = serializers.IntegerField()
    failed_calls_24h = serializers.IntegerField()
