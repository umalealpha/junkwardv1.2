"""integrations/serializers.py"""
from rest_framework import serializers
from .models import IntegrationEvent


class IntegrationEventSerializer(serializers.ModelSerializer):
    class Meta:
        model  = IntegrationEvent
        fields = [
            'id', 'source_system', 'event_type', 'event_data',
            'status', 'error_message', 'processed_at',
            'result_type', 'result_id',
            'received_at', 'retry_count',
            'idempotency_key', 'received_via',
        ]
        read_only_fields = [
            'id', 'status', 'error_message', 'processed_at',
            'result_type', 'result_id', 'received_at', 'retry_count',
            # Attribution is stamped from the authenticated key, never accepted
            # from the request body — a sender must not be able to claim it is
            # somebody else.
            'received_via',
        ]


class IntegrationEventCreateSerializer(serializers.ModelSerializer):
    """Used for the inbound POST from Graphite."""
    class Meta:
        model  = IntegrationEvent
        fields = ['id', 'source_system', 'event_type', 'event_data', 'status',
                  'received_at', 'idempotency_key']
        read_only_fields = ['id', 'status', 'received_at']
