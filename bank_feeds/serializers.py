"""bank_feeds/serializers.py"""

from rest_framework import serializers

from .models import BankFeedConfig, BankFeedRun


class BankFeedConfigSerializer(serializers.ModelSerializer):
    bank_account_code = serializers.CharField(source='bank_account.code', read_only=True)
    bank_account_name = serializers.CharField(source='bank_account.name', read_only=True)
    protocol_display = serializers.CharField(source='get_protocol_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = BankFeedConfig
        fields = [
            'id', 'name',
            'bank_account', 'bank_account_code', 'bank_account_name',
            'protocol', 'protocol_display',
            'env_prefix', 'remote_path', 'file_pattern',
            'schedule_cron',
            'last_run_at', 'last_run_status',
            'status', 'status_display', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at',
            'last_run_at', 'last_run_status',
        ]


class BankFeedRunSerializer(serializers.ModelSerializer):
    config_name = serializers.CharField(source='config.name', read_only=True)
    outcome_display = serializers.CharField(source='get_outcome_display', read_only=True)
    triggered_by_username = serializers.CharField(
        source='triggered_by.username', read_only=True, default=None,
    )

    class Meta:
        model = BankFeedRun
        fields = [
            'id', 'config', 'config_name',
            'triggered_by', 'triggered_by_username',
            'started_at', 'finished_at',
            'outcome', 'outcome_display',
            'files_seen', 'files_processed',
            'statements_created', 'lines_created', 'duplicates_skipped',
            'error_message',
            'file_hashes',
            'created_at',
        ]
        read_only_fields = fields
