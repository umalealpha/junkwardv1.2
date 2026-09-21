"""documents/serializers.py"""

from rest_framework import serializers

from .models import AIActivityLog, DocumentUpload, VendorMapping


class DocumentUploadSerializer(serializers.ModelSerializer):
    uploaded_by_username = serializers.CharField(
        source='uploaded_by.username', read_only=True
    )

    class Meta:
        model = DocumentUpload
        fields = [
            'id', 'original_filename', 'file_type', 'file_size', 'file_hash',
            'status', 'document_type', 'classification_confidence',
            'extraction_method', 'ai_explanation', 'user_decision',
            'suggested_action', 'error_message',
            'uploaded_by', 'uploaded_by_username',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'file_hash', 'file_type', 'file_size', 'status',
            'document_type', 'classification_confidence', 'extraction_method',
            'ai_explanation', 'user_decision', 'suggested_action',
            'error_message', 'uploaded_by', 'created_at', 'updated_at',
        ]


class DocumentDetailSerializer(serializers.ModelSerializer):
    uploaded_by_username = serializers.CharField(
        source='uploaded_by.username', read_only=True
    )
    activity_logs = serializers.SerializerMethodField()
    resulting_object_kind = serializers.SerializerMethodField()

    class Meta:
        model = DocumentUpload
        fields = [
            'id', 'original_filename', 'file_type', 'file_size', 'file_hash',
            'status', 'document_type', 'classification_confidence',
            'extraction_method', 'extracted_text', 'extracted_data',
            'suggested_action', 'ai_explanation',
            'user_decision', 'user_corrections',
            'resulting_object_id', 'resulting_object_kind', 'error_message',
            'uploaded_by', 'uploaded_by_username',
            'processed_by', 'created_at', 'updated_at',
            'activity_logs',
        ]
        read_only_fields = fields

    def get_activity_logs(self, obj):
        logs = obj.activity_logs.all()[:10]
        return AIActivityLogSerializer(logs, many=True).data

    def get_resulting_object_kind(self, obj):
        """Return 'invoice' / 'payment' / etc so the UI can build a link."""
        if obj.resulting_content_type_id:
            return obj.resulting_content_type.model
        return None


class AIActivityLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIActivityLog
        fields = [
            'id', 'action_type', 'input_data', 'output_data',
            'confidence', 'processing_time_ms', 'created_at',
        ]
        read_only_fields = fields


class VendorMappingSerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(
        source='contact.name', read_only=True
    )

    class Meta:
        model = VendorMapping
        fields = [
            'id', 'vendor_pattern', 'contact', 'contact_name',
            'default_account', 'match_count', 'auto_apply',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'match_count', 'auto_apply', 'created_at', 'updated_at']
