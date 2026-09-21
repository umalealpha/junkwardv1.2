"""hris/document_views.py — HR document vault (Dorothy 2026-06-26).

A simple file store for the documents new joiners sign (onboarding pack +
signed agreements), with categories for the exit-interview / disciplinary docs
to come. Files live in media/hr_documents/. HR-gated via the standard HRIS
policy (`_gate`) — the vault holds personal signed agreements (employee PII).

    GET  /api/v1/hris/documents/                 list (optional ?category=)
    POST /api/v1/hris/documents/                 upload (multipart: file, title,
                                                  category, description,
                                                  is_personal, employee_name)
    GET  /api/v1/hris/documents/<id>/download/    stream the file
"""
from __future__ import annotations

from django.http import FileResponse, Http404
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from hris.models import HRDocument
from hris.feature_views import _gate
from django.utils import timezone

VALID_CATEGORIES = {c[0] for c in HRDocument.Category.choices}


def _serialize(d, request) -> dict:
    return {
        'id': str(d.id),
        'title': d.title,
        'category': d.category,
        'category_label': d.get_category_display(),
        'description': d.description,
        'is_personal': d.is_personal,
        'employee_name': d.employee_name,
        'filename': (d.file.name.split('/')[-1] if d.file else ''),
        'size': (d.file.size if d.file else 0),
        'download_url': request.build_absolute_uri(f'/api/v1/hris/documents/{d.id}/download/'),
        'uploaded_by': (d.uploaded_by.username if d.uploaded_by_id else None),
        'created_at': d.created_at.isoformat(),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def documents(request):
    denied = _gate(request)         # HR whitelist + unlock (vault holds PII)
    if denied:
        return denied

    if request.method == 'GET':
        qs = HRDocument.objects.all()
        cat = (request.query_params.get('category') or '').strip()
        if cat:
            qs = qs.filter(category=cat)
        rows = [_serialize(d, request) for d in qs]
        return Response({
            'count': len(rows),
            'documents': rows,
            'categories': [{'value': c[0], 'label': c[1]} for c in HRDocument.Category.choices],
        })

    # POST — upload (a file OR pasted text, e.g. a job description)
    category = (request.data.get('category') or 'onboarding').strip()
    if category not in VALID_CATEGORIES:
        category = 'other'

    # Optional link to the employee the document is about (their HRISProfile).
    # This is what makes it visible to that employee + their line manager. The
    # front-end staff picker sends the payroll Employee id (`employee_pk`); older
    # callers send the HRISProfile id (`employee_id`). Support both.
    from hris.models import HRISProfile
    employee = None
    picked_name = ''
    employee_pk = (request.data.get('employee_pk') or '').strip()
    employee_id = (request.data.get('employee_id') or '').strip()
    if employee_pk:
        employee = HRISProfile.objects.filter(employee_id=employee_pk).first()
        from payroll.models import Employee
        emp = Employee.objects.filter(pk=employee_pk).first()
        picked_name = emp.full_name if emp else ''
    elif employee_id:
        employee = HRISProfile.objects.filter(pk=employee_id).first()
    employee_name = (request.data.get('employee_name') or picked_name
                     or (employee.employee.full_name if employee and employee.employee_id else '')).strip()[:200]

    is_personal = str(request.data.get('is_personal', '')).lower() in ('1', 'true', 'yes', 'on')
    description = (request.data.get('description') or '').strip()
    title = (request.data.get('title') or '').strip()[:200]

    f = request.FILES.get('file')
    text_content = (request.data.get('text_content') or '').strip()

    if f is not None:
        if not title:
            title = (getattr(f, 'name', '') or 'Untitled').strip()[:200]
        file_obj = f
    elif text_content:
        # Pasted text → render a branded PDF so it stores like any other vault
        # document (viewable, printable, downloadable). Job descriptions use the
        # JD layout; any other pasted note reuses the same letterhead.
        from django.core.files.base import ContentFile
        from django.utils import timezone
        from hris.jobdesc_pdf import render_job_description_pdf
        job_title = (request.data.get('job_title') or '').strip()[:120]
        if not title:
            if category == 'job_description':
                title = f"Job Description — {employee_name or job_title or 'Role'}"[:200]
            else:
                title = f"{dict(HRDocument.Category.choices).get(category, 'Document')} — {employee_name or 'note'}"[:200]
        pdf_bytes = render_job_description_pdf(
            employee_name=employee_name,
            job_title=(job_title or (title if category != 'job_description' else '')),
            body_text=text_content,
            issued_date=timezone.localdate(),
        )
        safe = ''.join(ch if ch.isalnum() or ch in ' -_' else '_' for ch in title).strip()[:80] or 'document'
        file_obj = ContentFile(pdf_bytes, name=f'{safe}.pdf')
    else:
        return Response({'detail': 'Attach a file, or paste the text (e.g. the job description) in the "text_content" field.'},
                        status=status.HTTP_400_BAD_REQUEST)

    d = HRDocument(
        title=title or 'Untitled', category=category, file=file_obj,
        description=description,
        is_personal=is_personal,
        employee=employee,
        employee_name=employee_name,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    d.save(audit_user=request.user)
    return Response(_serialize(d, request), status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def document_download(request, pk):
    d = (HRDocument.objects
         .select_related('employee', 'employee__employee', 'employee__manager')
         .filter(pk=pk).first())
    if d is None or not d.file:
        raise Http404('Document not found.')
    # Access is per-document: the subject, their line manager, the C-suite, HR.
    from hris.document_access import can_access_hr_document
    if not can_access_hr_document(request.user, d):
        return Response({'detail': 'You do not have access to this document.'},
                        status=status.HTTP_403_FORBIDDEN)
    return FileResponse(d.file.open('rb'), as_attachment=True,
                        filename=(d.file.name.split('/')[-1] or 'document'))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_documents(request):
    """GET /api/v1/hris/my-documents/ — the documents the caller may see:
    their own (e.g. their Development Dialogue), plus their direct reports' if
    they are a line manager, plus everything for HR / the C-suite. Self-service
    gated (any employee), then scoped by hris.document_access.
    """
    denied = _gate(request, capability='view_self')
    if denied:
        return denied
    from hris.document_access import visible_hr_documents
    qs = (visible_hr_documents(request.user)
          .select_related('employee', 'employee__employee')
          .order_by('-created_at'))
    rows = [_serialize(d, request) for d in qs]
    return Response({'count': len(rows), 'documents': rows})
