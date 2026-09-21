"""
reinsurance/document_views.py — the KYC / evidence register for a counterparty.

Arun P. Iyer's control brief §5 (15-Sep-2026): an authenticated document
register with type, filename, issue and expiry dates, source, uploader,
verifier and verification status, plus secure open/download.

  GET    /api/v1/reinsurance/reinsurers/<id>/documents/     list + the gaps
  POST   /api/v1/reinsurance/reinsurers/<id>/documents/     upload (multipart)
  POST   /api/v1/reinsurance/documents/<doc_id>/verify/     verify / reject
  GET    /api/v1/reinsurance/documents/<doc_id>/download/   stream the file

🔴 `/media/` IS NOT SERVED IN THIS DEPLOYMENT. `document.file.url` returns a 404
in production — `alpha_finance/urls.py` serves media through Django's `static()`
helper, which does nothing when `DEBUG=False`. That trap cost most of a session
on 16-Sep-2026 on the salvage photos. So the file is NEVER handed out as a URL:
it is streamed through `document_download`, behind the same permission as the
rest of the reinsurance controls.

Upload safety, in the order it matters:
  * extension ALLOWLIST — the browser's own `content_type` is caller-supplied
    and is never trusted or stored;
  * the served content type is derived from the extension we allowed, and
    anything we would not serve inline is served as an attachment, so a file
    that manages to carry markup cannot execute against Omni's origin;
  * a size ceiling, refused with 413 rather than swallowed;
  * the original filename is kept for the register but sanitised before it ever
    reaches a Content-Disposition header.
"""
from __future__ import annotations

import os
import re

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import (api_view, parser_classes,
                                       permission_classes)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import user_has_permission
from reinsurance.models import Reinsurer, ReinsurerDocument, evidence_gaps

#: What may be uploaded, and what each is served as. Nothing outside this map is
#: accepted, and nothing is served as a type the caller chose.
ALLOWED: dict[str, str] = {
    '.pdf':  'application/pdf',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.doc':  'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls':  'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
#: Served inline in the browser. Everything else downloads — an HTML-ish payload
#: rendered inline would run against Omni's own origin.
INLINE = {'application/pdf', 'image/png', 'image/jpeg'}
MAX_BYTES = 25 * 1024 * 1024

VIEW_PERM = 're.view'
#: Uploading evidence is underwriting/compliance work; VERIFYING it is the
#: compliance sign-off itself, and reuses the permission the approval chain
#: already gates its compliance stage on rather than inventing a second one.
UPLOAD_PERM = 're.view'
VERIFY_PERM = 'compliance.counterparty.approve'


def _require(user, perm: str):
    if not user_has_permission(user, perm):
        raise PermissionDenied('You do not have access to the reinsurance controls.')


def _safe_name(name: str) -> str:
    """A filename safe to put in a header — no quotes, no path, no newlines."""
    base = os.path.basename(name or '').strip()
    base = re.sub(r'[^A-Za-z0-9._ \-]', '_', base)
    return base[:120] or 'document'


def _serialize(doc: ReinsurerDocument, on=None) -> dict:
    return {
        'id': str(doc.pk),
        'kind': doc.kind,
        'kind_label': doc.get_kind_display(),
        'title': doc.title or doc.original_filename,
        'original_filename': doc.original_filename,
        'content_type': doc.content_type,
        'size_bytes': doc.size_bytes,
        # None and 0 are different facts (controls_api._d, same rule).
        'issue_date': doc.issue_date.isoformat() if doc.issue_date else None,
        'expiry_date': doc.expiry_date.isoformat() if doc.expiry_date else None,
        'expired': doc.is_expired(on),
        'source': doc.source or None,
        'uploaded_by': getattr(doc.uploaded_by, 'get_full_name', lambda: '')()
                       or getattr(doc.uploaded_by, 'username', None),
        'uploaded_at': doc.created_at.isoformat() if doc.created_at else None,
        'verification_status': doc.verification_status,
        'verification_label': doc.get_verification_status_display(),
        'verified_by': getattr(doc.verified_by, 'get_full_name', lambda: '')()
                       or getattr(doc.verified_by, 'username', None),
        'verified_at': doc.verified_at.isoformat() if doc.verified_at else None,
        'verification_note': doc.verification_note or None,
        'counts_as_evidence': doc.counts_as_evidence(on),
        'required': doc.kind in ReinsurerDocument.REQUIRED_KINDS,
        'download_url': f'/api/v1/reinsurance/documents/{doc.pk}/download/',
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def reinsurer_documents(request, reinsurer_id):
    reinsurer = Reinsurer.objects.filter(pk=reinsurer_id).first()
    if reinsurer is None:
        raise Http404('Counterparty not found.')

    if request.method == 'GET':
        _require(request.user, VIEW_PERM)
        today = timezone.localdate()
        docs = list(reinsurer.documents.select_related('uploaded_by', 'verified_by'))
        return Response({
            'reinsurer': {'id': str(reinsurer.pk), 'name': reinsurer.name,
                          'short_code': reinsurer.short_code,
                          'approval_status': reinsurer.approval_status},
            'documents': [_serialize(d, today) for d in docs],
            # The gaps are the point of the register. They are reported even
            # when the list is empty — an empty register is the WORST case, not
            # a quiet one, and must not render as "nothing to see".
            'gaps': evidence_gaps(reinsurer, today),
            'kinds': [{'value': k, 'label': lbl,
                       'required': k in ReinsurerDocument.REQUIRED_KINDS}
                      for k, lbl in ReinsurerDocument.Kind.choices],
        })

    _require(request.user, UPLOAD_PERM)
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach the document file.'},
                        status=status.HTTP_400_BAD_REQUEST)

    name = getattr(f, 'name', '') or 'document'
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED:
        return Response(
            {'detail': f'{ext or "That file type"} cannot be uploaded. '
                       f'Accepted: {", ".join(sorted(ALLOWED))}.'},
            status=status.HTTP_400_BAD_REQUEST)
    if (f.size or 0) > MAX_BYTES:
        return Response({'detail': f'{_safe_name(name)} is larger than 25 MB.'},
                        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    kind = (request.data.get('kind') or '').strip()
    if kind not in dict(ReinsurerDocument.Kind.choices):
        return Response({'detail': 'Say which kind of document this is.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # A multipart form sends dates as STRINGS. Assigning one straight to a
    # DateField saves happily and then blows up on `.isoformat()` in the
    # serialiser — a 500 AFTER the row has committed, so a retry leaves a
    # phantom document behind. Same trap as the disciplinary 500 (PR #614,
    # memory r-date-string), proven again here by the ship-gate on 16-Sep-2026.
    dates = {}
    for field in ('issue_date', 'expiry_date'):
        raw = (request.data.get(field) or '').strip()
        if not raw:
            dates[field] = None
            continue
        parsed = parse_date(raw)
        if parsed is None:
            return Response(
                {'detail': f'"{raw}" is not a date. Use YYYY-MM-DD, e.g. 2027-01-31.'},
                status=status.HTTP_400_BAD_REQUEST)
        dates[field] = parsed

    doc = ReinsurerDocument(
        reinsurer=reinsurer,
        kind=kind,
        title=(request.data.get('title') or '').strip()[:200],
        file=f,
        original_filename=_safe_name(name),
        # Derived from the extension we allowed — never `f.content_type`.
        content_type=ALLOWED[ext],
        size_bytes=f.size or 0,
        issue_date=dates['issue_date'],
        expiry_date=dates['expiry_date'],
        source=(request.data.get('source') or '').strip()[:200],
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    doc.save(audit_user=request.user)
    return Response(_serialize(doc), status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_document(request, doc_id):
    """Compliance says they have read it. Until then it is not evidence."""
    _require(request.user, VERIFY_PERM)
    doc = ReinsurerDocument.objects.filter(pk=doc_id).first()
    if doc is None:
        raise Http404('Document not found.')

    decision = (request.data.get('status') or '').strip().lower()
    if decision not in (ReinsurerDocument.Verification.VERIFIED,
                        ReinsurerDocument.Verification.REJECTED):
        return Response({'detail': 'Say "verified" or "rejected".'},
                        status=status.HTTP_400_BAD_REQUEST)
    note = (request.data.get('note') or '').strip()
    if decision == ReinsurerDocument.Verification.REJECTED and not note:
        # Same rule the approval workflow already applies to a return or a
        # rejection: a block with no stated reason is what people escalate
        # around, so it is refused rather than stored empty.
        return Response({'detail': 'Say why it is rejected.'},
                        status=status.HTTP_400_BAD_REQUEST)

    doc.verification_status = decision
    doc.verification_note = note
    doc.verified_by = request.user
    doc.verified_at = timezone.now()
    doc.save(audit_user=request.user)
    return Response(_serialize(doc))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def document_download(request, doc_id):
    _require(request.user, VIEW_PERM)
    doc = ReinsurerDocument.objects.filter(pk=doc_id).first()
    if doc is None or not doc.file:
        raise Http404('Document not found.')
    ctype = doc.content_type or 'application/octet-stream'
    return FileResponse(
        doc.file.open('rb'),
        as_attachment=ctype not in INLINE,
        filename=doc.original_filename or 'document',
        content_type=ctype,
    )
