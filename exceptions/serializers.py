"""exceptions/serializers.py — DRF serializers."""

from rest_framework import serializers

from .models import Exception as ExceptionModel


class ExceptionListSerializer(serializers.ModelSerializer):
    severity_display       = serializers.CharField(source='get_severity_display', read_only=True)
    status_display         = serializers.CharField(source='get_status_display', read_only=True)
    exception_type_display = serializers.CharField(source='get_exception_type_display', read_only=True)
    requires_role_display  = serializers.CharField(source='get_requires_role_display', read_only=True)

    class Meta:
        model  = ExceptionModel
        fields = [
            'id', 'exception_type', 'exception_type_display',
            'severity', 'severity_display',
            'status', 'status_display',
            'requires_role', 'requires_role_display',
            'title', 'source_label',
            'created_at',
        ]


class ExceptionDetailSerializer(serializers.ModelSerializer):
    severity_display       = serializers.CharField(source='get_severity_display', read_only=True)
    status_display         = serializers.CharField(source='get_status_display', read_only=True)
    exception_type_display = serializers.CharField(source='get_exception_type_display', read_only=True)
    requires_role_display  = serializers.CharField(source='get_requires_role_display', read_only=True)
    acknowledged_by_username = serializers.CharField(
        source='acknowledged_by.username', read_only=True,
    )
    resolved_by_username  = serializers.CharField(source='resolved_by.username', read_only=True)
    dismissed_by_username = serializers.CharField(source='dismissed_by.username', read_only=True)
    created_by_username   = serializers.CharField(source='created_by.username', read_only=True)
    is_open               = serializers.BooleanField(read_only=True)

    class Meta:
        model  = ExceptionModel
        fields = [
            'id',
            'exception_type', 'exception_type_display',
            'severity',       'severity_display',
            'status',         'status_display',
            'requires_role',  'requires_role_display',
            'title', 'description',
            'source_app', 'source_model', 'source_id', 'source_label',
            'metadata',
            'acknowledged_by', 'acknowledged_by_username', 'acknowledged_at',
            'resolved_by',     'resolved_by_username',     'resolved_at',
            'resolution_notes',
            'dismissed_by',    'dismissed_by_username',    'dismissed_at',
            'dismissal_reason',
            'linker_notified_at', 'linker_response',
            'created_by', 'created_by_username',
            'is_open',
            'created_at', 'updated_at',
        ]


class ExceptionCreateSerializer(serializers.ModelSerializer):
    """For manual exception creation. The signal-driven flow uses
    `services.create_exception()` directly."""

    class Meta:
        model  = ExceptionModel
        fields = [
            'id',   # create must identify the new row (2026-09-10 sweep)
            'exception_type', 'severity', 'requires_role',
            'title', 'description',
            'source_app', 'source_model', 'source_id', 'source_label',
            'metadata',
        ]
        read_only_fields = ['id']

    def create(self, validated_data):
        from .services import create_exception
        request = self.context.get('request')
        return create_exception(
            created_by=request.user if request else None,
            **validated_data,
            dedupe=False,  # manual creation always lands a new row
        )
