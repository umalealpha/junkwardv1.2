from django.contrib import admin

from .models import AIActivityLog, DocumentUpload, VendorMapping


@admin.register(DocumentUpload)
class DocumentUploadAdmin(admin.ModelAdmin):
    list_display = [
        'original_filename', 'document_type', 'status',
        'classification_confidence', 'extraction_method',
        'user_decision', 'uploaded_by', 'created_at',
    ]
    list_filter = ['status', 'document_type', 'extraction_method', 'user_decision']
    search_fields = ['original_filename', 'file_hash']
    readonly_fields = [
        'id', 'file_hash', 'extracted_text', 'extracted_data',
        'suggested_action', 'ai_explanation', 'user_corrections',
        'created_at', 'updated_at',
    ]


@admin.register(VendorMapping)
class VendorMappingAdmin(admin.ModelAdmin):
    list_display = [
        'vendor_pattern', 'contact', 'default_account',
        'match_count', 'auto_apply',
    ]
    list_filter = ['auto_apply']
    search_fields = ['vendor_pattern']


@admin.register(AIActivityLog)
class AIActivityLogAdmin(admin.ModelAdmin):
    list_display = [
        'document_upload', 'action_type', 'confidence',
        'processing_time_ms', 'created_at',
    ]
    list_filter = ['action_type']
    readonly_fields = ['input_data', 'output_data']
