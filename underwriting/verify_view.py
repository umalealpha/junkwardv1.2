"""
underwriting/verify_view.py — public quotation verification page.

The page the QR code on a quotation opens. A client or broker scans the code and
lands here to confirm the document in their hand is a GENUINE Alpha Direct
quotation — the answer to "is this really your quote, and are these really your
figures?". View-only, no login.

Mirrors the proven PO verification page (procurement/verify_view.py): the link
carries the quotation's UUID primary key, so it is a capability URL — only
someone holding the printed document has it, nothing can be enumerated, and the
page discloses nothing that is not already printed on that document.

The client name IS on the document the scanner is holding, so echoing it back is
what makes the check meaningful; nothing else about the client is exposed.
"""
import base64
import uuid
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from procurement.pdf import _logo_round_path
from .models import Quote

# Client-facing status wording. A quotation the client holds is either a live
# offer, or it is not — internal outcome labels ("Converted to policy") say more
# about our pipeline than the client needs.
_STATUS_LABEL = {
    Quote.Status.DRAFT:  'Draft — not yet issued',
    Quote.Status.ISSUED: 'Issued',
    Quote.Status.LAPSED: 'Lapsed',
    Quote.Status.WON:    'Accepted',
    Quote.Status.LOST:   'No longer current',
}


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    p = _logo_round_path()
    if not p:
        return ''
    return 'data:image/png;base64,' + base64.b64encode(Path(p).read_bytes()).decode('ascii')


@require_GET
def quote_verify(request, code):
    base = {'logo': _logo_data_uri(), 'public_base': settings.PUBLIC_BASE_URL}
    try:
        q = Quote.objects.get(pk=uuid.UUID(str(code)))
    except (ValueError, Quote.DoesNotExist):
        return render(request, 'underwriting/quote_verify.html',
                      {**base, 'ok': False}, status=404)

    expired = bool(q.valid_until and q.valid_until < timezone.localdate())
    ctx = {
        **base,
        'ok': True,
        'number': q.quote_number,
        'client': q.client_name or '—',
        'class_of_business': q.class_of_business or '—',
        'period': q.period or '—',
        # The figure that matters to a client checking a document: what is payable.
        'total': f'{q.total:,.2f}',
        'issued': (q.issued_at or q.created_at).strftime('%d %b %Y'),
        'valid_until': q.valid_until.strftime('%d %b %Y') if q.valid_until else '—',
        'status': _STATUS_LABEL.get(q.status, q.get_status_display()),
        'expired': expired and q.status == Quote.Status.ISSUED,
    }
    return render(request, 'underwriting/quote_verify.html', ctx, status=200)
