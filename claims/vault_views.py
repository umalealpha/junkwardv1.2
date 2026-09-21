"""
claims/vault_views.py

Claim Forms Vault API.

  GET  /api/v1/claim-forms/                 list (any authenticated staff)
  GET  /api/v1/claim-forms/<id>/download/   stream the PDF (authed)
  POST /api/v1/claim-forms/upload/          add / replace a form (admin only)

Downloads are STREAMED through an authenticated view, never served from
MEDIA_URL. Two hard-won traps this avoids:
  - /media/ is not served in this deployment — a raw file.url 404s;
  - the SPA authenticates with a bearer token in localStorage, so a plain
    <a href> download 401s. The frontend fetches this endpoint with the token
    and saves the blob.
"""

from django.http import FileResponse, Http404
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from core.models import AuditLog

from .vault_models import ClaimForm

_MAX_BYTES = 15 * 1024 * 1024  # 15 MB — a claim form is a small PDF
_ALLOWED_CT = {'application/pdf'}


def _safe_size_kb(f):
    """File size in KB, or None if the blob is gone from storage.

    FileField is truthy even when the underlying file is missing, and .size
    stats storage and RAISES if it isn't there — one missing file would 500 the
    whole list for every staff member. Given this deployment's media-volume
    history, degrade to None rather than crash the page.
    """
    try:
        return round((f.file.size or 0) / 1024) if f.file else None
    except Exception:
        return None


def _serialize(f) -> dict:
    return {
        'id':          str(f.id),
        'slug':        f.slug,
        'title':       f.title,
        'category':    f.category,
        'categoryLabel': f.get_category_display(),
        'description': f.description,
        'active':      f.active,
        'sizeKb':      _safe_size_kb(f),
        'uploadedBy':  getattr(f.uploaded_by, 'get_full_name', lambda: '')() or getattr(f.uploaded_by, 'username', '') if f.uploaded_by else '',
        'updatedAt':   f.updated_at.isoformat() if f.updated_at else None,
        'downloadUrl': f'/api/v1/claim-forms/{f.id}/download/',
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def claim_forms(request):
    """The vault. Blank templates only — no PII — so all staff may read it."""
    qs = ClaimForm.objects.all()
    if request.query_params.get('active_only', '1') == '1':
        qs = qs.filter(active=True)
    cat = request.query_params.get('category')
    if cat:
        qs = qs.filter(category=cat)

    rows = [_serialize(f) for f in qs]

    # Group counts for the page's category rail.
    by_cat = {}
    for f in ClaimForm.objects.filter(active=True):
        by_cat[f.category] = by_cat.get(f.category, 0) + 1

    return Response({
        'data': rows,
        'summary': {
            'total':      len(rows),
            'byCategory': by_cat,
            'categories': [{'key': k, 'label': lbl} for k, lbl in ClaimForm.Category.choices],
        },
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def claim_form_download(request, pk):
    try:
        f = ClaimForm.objects.get(pk=pk)
    except ClaimForm.DoesNotExist:
        raise Http404

    if not f.file:
        raise Http404

    AuditLog.objects.create(
        table_name='claims_claimform',
        record_id=str(f.id),
        action=AuditLog.Action.DOWNLOAD,
        new_values={'slug': f.slug, 'by': getattr(request.user, 'username', '')},
    )

    filename = f'{f.slug}.pdf' if not f.slug.endswith('.pdf') else f.slug
    return FileResponse(f.file.open('rb'), as_attachment=True, filename=filename)


@api_view(['POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def claim_form_upload(request):
    """Add a new form or replace an existing one (matched on slug). Admin only —
    this writes the official template the whole company relies on."""
    up = request.FILES.get('file')
    slug = (request.data.get('slug') or '').strip().lower()
    title = (request.data.get('title') or '').strip()

    if not up:
        return Response({'message': 'Attach a PDF.'}, status=422)
    if not slug or not title:
        return Response({'message': 'A slug and a title are both required.'}, status=422)
    if (up.size or 0) > _MAX_BYTES:
        return Response({'message': 'That file is larger than 15 MB.'}, status=422)
    # The client-supplied content_type is a claim, not proof — check the bytes.
    if getattr(up, 'content_type', '') not in _ALLOWED_CT:
        return Response({'message': 'Only PDF claim forms are accepted.'}, status=422)
    head = up.read(5)
    up.seek(0)
    if head != b'%PDF-':
        return Response({'message': 'That file does not look like a PDF.'}, status=422)

    # Validate category against the model's own choices — Django does NOT enforce
    # choices on .save(), so a bad value would make the form vanish from every
    # category filter while still showing under "All".
    category = request.data.get('category')
    if category and category not in ClaimForm.Category.values:
        return Response({'message': 'Unknown category.'}, status=422)

    form, created = ClaimForm.objects.get_or_create(slug=slug, defaults={'title': title})
    form.title = title
    if category:
        form.category = category
    if request.data.get('description') is not None:
        form.description = request.data['description']
    form.source_name = getattr(up, 'name', '') or form.source_name
    form.file = up
    form.uploaded_by = request.user if request.user.is_authenticated else None
    form.active = True
    form.save()

    return Response({'data': _serialize(form), 'created': created})
