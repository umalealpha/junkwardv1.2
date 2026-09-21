"""
documents/models.py

AI-powered document processing for Alpha Direct Financial Management System.

Models:
  - DocumentUpload    Uploaded files with extraction pipeline state
  - VendorMapping     Learned vendor-to-contact mappings for auto-matching
  - AIActivityLog     Audit trail for all AI/rule-based processing activity
"""

import uuid

from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


# ---------------------------------------------------------------------------
# DocumentUpload
# ---------------------------------------------------------------------------

class DocumentUpload(models.Model):
    FILE_TYPE_CHOICES = [
        ('pdf', 'PDF'),
        ('image', 'Image'),
        ('csv', 'CSV'),
        ('xlsx', 'Excel'),
        ('unknown', 'Unknown'),
    ]
    STATUS_CHOICES = [
        ('uploading', 'Uploading'),
        ('processing', 'Processing'),
        ('classified', 'Classified'),
        ('extracted', 'Extracted'),
        ('suggested', 'Suggested'),
        ('confirmed', 'Confirmed'),
        ('rejected', 'Rejected'),
        ('error', 'Error'),
    ]
    DOCUMENT_TYPE_CHOICES = [
        ('invoice', 'Invoice'),
        ('receipt', 'Receipt'),
        ('bank_statement', 'Bank Statement'),
        ('expense', 'Expense'),
        ('credit_note', 'Credit Note'),
        ('unknown', 'Unknown'),
    ]
    EXTRACTION_METHOD_CHOICES = [
        ('rule_based', 'Rule-Based'),
        ('ai_assisted', 'AI-Assisted'),
        ('hybrid', 'Hybrid'),
    ]
    USER_DECISION_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('rejected', 'Rejected'),
        ('edited', 'Edited'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # File info
    file = models.FileField(upload_to='documents/%Y/%m/')
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default='unknown')
    file_size = models.IntegerField(default=0)
    file_hash = models.CharField(max_length=64, unique=True)

    # Processing state
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='uploading')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES, null=True, blank=True)
    classification_confidence = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    extraction_method = models.CharField(max_length=20, choices=EXTRACTION_METHOD_CHOICES, default='rule_based')

    # Extracted content
    extracted_text = models.TextField(null=True, blank=True)
    extracted_data = models.JSONField(null=True, blank=True)

    # Suggestion
    suggested_action = models.JSONField(null=True, blank=True)
    ai_explanation = models.TextField(null=True, blank=True)

    # User decision
    user_decision = models.CharField(max_length=20, choices=USER_DECISION_CHOICES, default='pending')
    user_corrections = models.JSONField(null=True, blank=True)

    # Resulting object (generic FK to invoice/payment/JE created from this doc)
    resulting_content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    resulting_object_id = models.UUIDField(null=True, blank=True)
    resulting_object = GenericForeignKey('resulting_content_type', 'resulting_object_id')

    # Error tracking
    error_message = models.TextField(null=True, blank=True)

    # Audit
    uploaded_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='document_uploads'
    )
    processed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents_processed'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.original_filename} ({self.status})"


# ---------------------------------------------------------------------------
# VendorMapping
# ---------------------------------------------------------------------------

class VendorMapping(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor_pattern = models.CharField(max_length=255, unique=True)
    contact = models.ForeignKey(
        'billing.Contact', on_delete=models.CASCADE, related_name='vendor_mappings'
    )
    default_account = models.ForeignKey(
        'ledger.Account', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendor_mappings'
    )
    match_count = models.IntegerField(default=0)
    auto_apply = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-match_count']

    def __str__(self):
        return f"{self.vendor_pattern} -> {self.contact.name}"


# ---------------------------------------------------------------------------
# AIActivityLog
# ---------------------------------------------------------------------------

class AIActivityLog(models.Model):
    ACTION_TYPE_CHOICES = [
        ('ai_extraction', 'AI Extraction'),
        ('ai_error', 'AI Error'),
        ('rule_extraction', 'Rule Extraction'),
        ('user_confirm', 'User Confirm'),
        ('user_reject', 'User Reject'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_upload = models.ForeignKey(
        DocumentUpload, on_delete=models.CASCADE, related_name='activity_logs'
    )
    action_type = models.CharField(max_length=20, choices=ACTION_TYPE_CHOICES)
    input_data = models.JSONField(null=True, blank=True)
    output_data = models.JSONField(null=True, blank=True)
    confidence = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    processing_time_ms = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.action_type} on {self.document_upload.original_filename}"
