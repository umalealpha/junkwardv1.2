"""
documents/views.py

DRF views for document upload, processing, confirmation, and rejection.
"""

import hashlib

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from rest_framework import generics, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AIActivityLog, DocumentUpload, VendorMapping
from .record_creator import create_record_for
from .serializers import DocumentDetailSerializer, DocumentUploadSerializer
from .services import DocumentProcessor


def _get_owned_upload(pk, user):
    """Fetch a DocumentUpload by pk, scoped to the requesting user.

    IDOR guard (audit 2026-06-11): confirm/reject acted on any UUID, so a
    user could post a journal entry / payment off another user's uploaded
    invoice. Staff/superusers keep full access; everyone else only their own.
    Raises DocumentUpload.DoesNotExist if not visible to this user.
    """
    qs = DocumentUpload.objects.all()
    if not (user.is_staff or user.is_superuser):
        qs = qs.filter(uploaded_by=user)
    return qs.get(pk=pk)


def _file_type_from_ext(filename):
    """Determine file_type choice from extension."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    mapping = {
        'pdf': 'pdf',
        'png': 'image', 'jpg': 'image', 'jpeg': 'image',
        'tiff': 'image', 'bmp': 'image',
        'csv': 'csv',
        'xlsx': 'xlsx', 'xls': 'xlsx',
    }
    return mapping.get(ext, 'unknown')


def _sha256(file_obj):
    """Compute SHA-256 hash of an uploaded file."""
    h = hashlib.sha256()
    for chunk in file_obj.chunks():
        h.update(chunk)
    file_obj.seek(0)  # Reset for Django to save
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

class DocumentUploadView(APIView):
    """POST /api/v1/documents/upload/ - Upload and process a document."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response(
                {'detail': 'No file provided. Use multipart form field "file".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Compute hash for duplicate detection
        file_hash = _sha256(uploaded_file)

        # Duplicate detection. CFO directive 2026-05-19: a prior FAILED
        # upload (status='error') must NOT block a retry — the user
        # reuploaded the same file specifically to recover from the
        # previous error (e.g. XLSX support fix in PR #166). Treat only
        # non-error rows as duplicates; reprocess error rows in place.
        existing = (DocumentUpload.objects
                    .filter(file_hash=file_hash)
                    .exclude(status='error')
                    .first())
        if existing:
            serializer = DocumentDetailSerializer(existing)
            return Response(
                {
                    'detail': 'Duplicate file detected. Returning existing record.',
                    'duplicate': True,
                    'document': serializer.data,
                },
                status=status.HTTP_200_OK,
            )

        # Reprocess path — same hash but status=error → rerun in place
        # so we don't pile up error rows on every retry.
        retry = (DocumentUpload.objects
                 .filter(file_hash=file_hash, status='error')
                 .first())
        if retry is not None:
            retry.file = uploaded_file
            retry.original_filename = uploaded_file.name
            retry.file_type = _file_type_from_ext(uploaded_file.name)
            retry.file_size = uploaded_file.size
            retry.status = 'uploading'
            retry.error_message = ''
            retry.uploaded_by = request.user
            retry.save()
            upload = retry
        else:
            upload = DocumentUpload(
                file=uploaded_file,
                original_filename=uploaded_file.name,
                file_type=_file_type_from_ext(uploaded_file.name),
                file_size=uploaded_file.size,
                file_hash=file_hash,
                status='uploading',
                uploaded_by=request.user,
            )
            upload.save()

        # Process synchronously
        try:
            processor = DocumentProcessor()
            processor.process(upload.id)
        except Exception:
            pass  # Error is recorded on the upload object by the processor

        upload.refresh_from_db()
        serializer = DocumentDetailSerializer(upload)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class DocumentListView(generics.ListAPIView):
    """GET /api/v1/documents/ - List uploads for the current user."""
    permission_classes = [IsAuthenticated]
    serializer_class = DocumentUploadSerializer

    def get_queryset(self):
        qs = DocumentUpload.objects.filter(uploaded_by=self.request.user)
        # Filters
        doc_status = self.request.query_params.get('status')
        if doc_status:
            qs = qs.filter(status=doc_status)
        doc_type = self.request.query_params.get('document_type')
        if doc_type:
            qs = qs.filter(document_type=doc_type)
        from_date = self.request.query_params.get('from_date')
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)
        to_date = self.request.query_params.get('to_date')
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)
        return qs


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class DocumentDetailView(generics.RetrieveDestroyAPIView):
    """
    GET    /api/v1/documents/{id}/  - Retrieve a single upload with full data.
    DELETE /api/v1/documents/{id}/  - Delete an upload and its underlying file.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = DocumentDetailSerializer
    queryset = DocumentUpload.objects.all()
    lookup_field = 'pk'

    def get_queryset(self):
        # IDOR guard (audit 2026-06-11): the list view scopes to the caller,
        # but detail/delete did not — any authenticated user could read or
        # delete another user's uploaded document (and its file) by UUID.
        # Scope to the uploader; staff/superusers retain full access.
        qs = DocumentUpload.objects.all()
        u = self.request.user
        if u.is_staff or u.is_superuser:
            return qs
        return qs.filter(uploaded_by=u)

    def perform_destroy(self, instance):
        # Remove the file from disk before deleting the DB row.
        # AIActivityLog rows cascade-delete via the FK.
        if instance.file:
            instance.file.delete(save=False)
        instance.delete()


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

class DocumentConfirmView(APIView):
    """POST /api/v1/documents/{id}/confirm/ - Confirm (and optionally edit) a suggestion."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            upload = _get_owned_upload(pk, request.user)
        except DocumentUpload.DoesNotExist:
            return Response(
                {'detail': 'Document not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if upload.status not in ('suggested', 'extracted'):
            return Response(
                {'detail': f'Cannot confirm document in "{upload.status}" status.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Detect user edits
        edited_data = request.data.get('edited_data')
        edited_action_type = request.data.get('edited_action_type')
        edited_document_type = request.data.get('edited_document_type')
        original = upload.suggested_action or {}

        if edited_data or edited_action_type or edited_document_type:
            # Compute diff between original suggestion and user edits
            corrections = {}
            if edited_data:
                for key, val in edited_data.items():
                    orig_val = original.get('details', {}).get(key)
                    if str(val) != str(orig_val):
                        corrections[key] = {'original': orig_val, 'edited': val}
                # Persist edits to the canonical record so downstream sees
                # the corrected values, not the AI's original guess.
                merged_extracted = dict(upload.extracted_data or {})
                merged_extracted.update(edited_data)
                upload.extracted_data = merged_extracted
                merged_details = dict(original.get('details') or {})
                merged_details.update(edited_data)
                upload.suggested_action = {
                    **original,
                    'details': merged_details,
                }
            if edited_action_type:
                # User overrode the AI's classification (e.g. invoice ↔ payment).
                prior = (upload.suggested_action or {}).get('action_type')
                if prior != edited_action_type:
                    corrections['action_type'] = {
                        'original': prior, 'edited': edited_action_type,
                    }
                upload.suggested_action = {
                    **(upload.suggested_action or {}),
                    'action_type': edited_action_type,
                }
            if edited_document_type:
                if upload.document_type != edited_document_type:
                    corrections['document_type'] = {
                        'original': upload.document_type,
                        'edited': edited_document_type,
                    }
                upload.document_type = edited_document_type
            upload.user_corrections = corrections if corrections else None
            upload.user_decision = 'edited' if corrections else 'confirmed'
        else:
            upload.user_decision = 'confirmed'

        # Create downstream Finance record (Invoice / Payment) — drafted, not posted.
        # Wrapped in a transaction so a failed creation rolls back the
        # status/edit changes too: the doc stays "suggested" so the user can retry.
        created_label, created_obj, create_error = None, None, None
        try:
            with transaction.atomic():
                upload.status = 'confirmed'
                upload.processed_by = request.user
                upload.save()

                created_label, created_obj = create_record_for(upload, request.user)
                if created_obj is not None:
                    upload.resulting_content_type = ContentType.objects.get_for_model(
                        type(created_obj)
                    )
                    upload.resulting_object_id = created_obj.pk
                    upload.save(update_fields=[
                        'resulting_content_type', 'resulting_object_id'
                    ])
        except Exception as exc:
            create_error = str(exc) or type(exc).__name__
            # Roll back the confirm so the UI can show the error and let user retry
            upload.refresh_from_db()
            upload.status = 'suggested'
            upload.error_message = f"Confirm failed: {create_error}"
            upload.save(update_fields=['status', 'error_message'])
            return Response(
                {'detail': f'Confirm failed creating downstream record: {create_error}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Update vendor mapping for learning
        self._update_vendor_mapping(upload, request.user)

        # Log the user action
        AIActivityLog.objects.create(
            document_upload=upload,
            action_type='user_confirm',
            input_data=edited_data,
            output_data={
                'user_decision': upload.user_decision,
                'created_record_type': created_label,
                'created_record_id': str(created_obj.pk) if created_obj else None,
            },
            confidence=float(upload.classification_confidence or 0),
        )

        serializer = DocumentDetailSerializer(upload)
        return Response(serializer.data)

    def _update_vendor_mapping(self, upload, user):
        """
        Update vendor mapping based on confirmed document.

        Only updates an existing mapping (bumps match_count). New mappings
        require a real Contact FK — created elsewhere when the user actually
        links the document to a Contact.
        """
        data = upload.extracted_data or {}
        vendor_name = (
            data.get('vendor_name')
            or data.get('possible_vendor')
        )
        if not vendor_name:
            return

        normalized = vendor_name.strip().upper()
        mapping = VendorMapping.objects.filter(vendor_pattern=normalized).first()
        if mapping is None:
            return  # No existing mapping; nothing to learn from yet
        mapping.match_count += 1
        if mapping.match_count >= 3 and not mapping.auto_apply:
            mapping.auto_apply = True
        mapping.save()


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

class DocumentRejectView(APIView):
    """POST /api/v1/documents/{id}/reject/ - Reject a suggestion."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            upload = _get_owned_upload(pk, request.user)
        except DocumentUpload.DoesNotExist:
            return Response(
                {'detail': 'Document not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if upload.status not in ('suggested', 'extracted'):
            return Response(
                {'detail': f'Cannot reject document in "{upload.status}" status.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get('reason', '')

        upload.user_decision = 'rejected'
        upload.status = 'rejected'
        upload.processed_by = request.user
        upload.save()

        AIActivityLog.objects.create(
            document_upload=upload,
            action_type='user_reject',
            input_data={'reason': reason},
            output_data={},
            confidence=0,
        )

        serializer = DocumentDetailSerializer(upload)
        return Response(serializer.data)
