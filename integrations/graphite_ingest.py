"""
integrations/graphite_ingest.py — the Omni end of the Graphite analytics feed.

    POST /api/v1/graphite-ingest/
    Authorization: Bearer <GRAPHITE_INGEST_TOKEN>
    {"dataset": "weekly_update", "rows": [ {...}, ... ]}

Pramod built the Graphite side against this address on 6 August 2026 and shipped
it dormant because nothing existed here to receive it. This is that receiver,
built to the same Appendix A v1 shape: one-way, keyed, HTTPS-only,
snapshot-replace, no PII, no write-back.

Three deliberate choices:

* **Inert by design.** Arrival of a snapshot does not start anything. No signal,
  no task, no email, no arm switched on. The CFO's rule for Alpha Brain is
  arms-off, and a feed that can set something running is not arms-off.

* **The PII promise is enforced, not trusted.** The contract says the payload
  carries no personal data. Good contracts still get broken by a well-meaning
  extra column, and under the Data Protection Act the damage is done the moment
  it lands in our database. So EVERY row of every push is screened and a payload
  carrying what looks like an Omang, a bank account or a personal contact detail
  is REJECTED at the door with the offending field named — nothing is stored, and
  only the field name is logged, never the value.

* **Fails closed.** No token configured means every call is refused. An
  integration that quietly starts accepting anonymous pushes because a setting
  was missed is worse than one that is switched off.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re

from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from integrations.models import GraphiteSnapshot

logger = logging.getLogger('graphite-ingest')

# A snapshot is a spreadsheet tab, not a data warehouse. Anything larger is a
# mistake at the other end and should bounce rather than fill the disk.
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50_000
DATASET_RE = re.compile(r'^[a-z][a-z0-9_]{2,63}$')

# ── PII screen ────────────────────────────────────────────────────────────────
# Field NAMES that must never appear. Cheap, and catches the realistic accident:
# somebody adds a column to a Graphite view and the feed carries it along.
_BANNED_FIELDS = {
    'omang', 'id_number', 'idnumber', 'national_id', 'passport', 'passport_no',
    'bank_account', 'account_number', 'accountnumber', 'account_no', 'iban',
    'card_number', 'cardnumber', 'residential_address', 'home_address',
    'physical_address', 'street_address', 'postal_address', 'date_of_birth',
    'dob', 'birth_date', 'next_of_kin', 'medical', 'diagnosis', 'icd10',
    'phone', 'mobile', 'cell', 'msisdn', 'email', 'email_address',
    'first_name', 'last_name', 'full_name', 'surname', 'member_name',
    'insured_name', 'client_name', 'policyholder', 'policyholder_name',
}
# VALUE shapes, for a field innocently named "ref" that carries an Omang.
_OMANG_RE = re.compile(r'\b\d{9}\b')                     # Botswana national ID
_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_LONG_NUM_RE = re.compile(r'\b\d{10,}\b')                # bank / card / phone


def _value_reason(key, val) -> str | None:
    """Shape checks on a single value."""
    if val is None or isinstance(val, (int, float, bool)):
        return None
    s = str(val)
    if len(s) > 4096:
        return f'field "{key}" is too long to be a reporting value'
    if _EMAIL_RE.search(s):
        return f'field "{key}" contains an email address'
    if _OMANG_RE.search(s):
        return f'field "{key}" contains something shaped like an Omang number'
    if _LONG_NUM_RE.search(s):
        return (f'field "{key}" contains a long number that could be a bank '
                f'account, card or phone number')
    return None


def _pii_reason(rows) -> str | None:
    """Return a plain reason if the payload looks like it carries personal data.

    Names the field, never the value — a rejection message must not itself
    become the leak.

    EVERY row is screened. An earlier version sampled the first 2,000, which
    meant an Omang sitting at row 40,000 of a 50,000-row push went straight into
    the database. This is a Data Protection control, not a statistic: a screen
    that stops looking part-way through is exactly the "trusted, not enforced"
    posture the docstring above promises it is not. Three regexes over an 8 MB
    body cost milliseconds.
    """
    for row in rows:
        # A row that is not a flat object is not a spreadsheet row, and it used
        # to be skipped in silence — so a bare string "someone@example.co.bw",
        # or a nested {"meta": {"client_name": ...}}, sailed past the screen
        # untouched. Flat rows only; anything else is refused.
        if not isinstance(row, dict):
            reason = _value_reason('(row)', row)
            return reason or ('every row must be a flat object of columns and '
                              'values — this feed carries reporting tables, not '
                              'nested records')
        for key, val in row.items():
            if isinstance(val, (dict, list, tuple, set)):
                return (f'field "{key}" is nested. Send flat rows only — a nested '
                        f'value can hide personal data below the level this check '
                        f'can see')
            k = str(key).strip().lower().replace(' ', '_').replace('-', '_')
            if k in _BANNED_FIELDS:
                return f'field "{key}" is personal data and must not be sent'
            reason = _value_reason(key, val)
            if reason is not None:
                return reason
    return None


def _client_ip(request) -> str:
    """Cloudflare's value, which the caller cannot forge. Never X-Forwarded-For —
    the caller controls that one. Mirrors core.intel_summary.client_ip."""
    cf = (request.META.get('HTTP_CF_CONNECTING_IP') or '').strip()
    return cf or (request.META.get('REMOTE_ADDR') or '').strip()


def _token_ok(request) -> bool:
    """Constant-time bearer check. No token configured => refuse everything."""
    expected = (getattr(settings, 'GRAPHITE_INGEST_TOKEN', '') or '').strip()
    if not expected:
        return False
    got = (request.headers.get('Authorization') or '').replace('Bearer ', '').strip()
    if not got:
        return False
    # Compare BYTES. Django decodes headers as latin-1, so a header containing a
    # non-ASCII character makes compare_digest(str, str) raise TypeError — an
    # unhandled 500 that anybody on the internet could trigger at will, forever.
    return hmac.compare_digest(got.encode('utf-8', 'surrogateescape'),
                               expected.encode('utf-8'))


class GraphiteIngestThrottle(AnonRateThrottle):
    scope = 'graphite_ingest'


class GraphiteIngestView(APIView):
    """Receive one dataset snapshot from Graphite. One-way, keyed, inert."""

    # MUST stay empty. With AZURE_SSO_ENABLED=true on prod, AzureJWTAuthentication
    # raises on any non-JWT Bearer value, so DRF answers 403 before the view runs
    # and no shared-token integration can work. Proven on prod 2026-07-25 by the
    # intel feed; the shared-token check below is the only wall and it has to be
    # the one that runs.
    authentication_classes: list = []
    permission_classes = [AllowAny]
    # A new anonymous, internet-facing WRITE endpoint gets a rate limit. The
    # intel feed has none, but the intel feed only reads.
    throttle_classes = [GraphiteIngestThrottle]

    def post(self, request):
        if not _token_ok(request):
            logger.warning('graphite-ingest refused: bad or missing token ip=%s',
                           _client_ip(request) or '?')
            return Response({'detail': 'Unauthorised.'}, status=401)

        # HTTPS only. request.is_secure() honours SECURE_PROXY_SSL_HEADER, so it
        # reads the proxy's verdict rather than the hop into gunicorn.
        if not request.is_secure() and not settings.DEBUG:
            return Response({'detail': 'This endpoint accepts HTTPS only.'}, status=400)

        # Refuse on the DECLARED length first: request.body pulls the whole
        # payload into the worker's memory, and Django's own ceiling is 250 MB.
        # Checking afterwards means the memory is already spent.
        try:
            declared = int(request.META.get('CONTENT_LENGTH') or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_BODY_BYTES:
            return Response(
                {'detail': f'Snapshot too large ({declared} bytes). '
                           f'The limit is {MAX_BODY_BYTES} bytes.'}, status=413)

        body = request.body or b''      # still checked, for chunked transfers
        if len(body) > MAX_BODY_BYTES:
            return Response(
                {'detail': f'Snapshot too large ({len(body)} bytes). '
                           f'The limit is {MAX_BODY_BYTES} bytes.'}, status=413)

        data = request.data if isinstance(request.data, dict) else {}
        dataset = str(data.get('dataset') or '').strip().lower()
        rows = data.get('rows')

        if not DATASET_RE.match(dataset):
            return Response(
                {'detail': 'dataset must be a short lower-case key such as '
                           '"weekly_update" (letters, digits and underscores).'},
                status=400)
        if not isinstance(rows, list):
            return Response({'detail': 'rows must be a list.'}, status=400)
        if len(rows) > MAX_ROWS:
            return Response(
                {'detail': f'{len(rows)} rows exceeds the {MAX_ROWS} limit for one '
                           f'snapshot.'}, status=413)

        # The contract says no personal data. Check, do not assume — once it is
        # written it is a DPA problem whoever's fault it was.
        reason = _pii_reason(rows)
        if reason is not None:
            logger.error('graphite-ingest REJECTED dataset=%s: %s', dataset, reason)
            return Response(
                {'detail': f'Rejected — this feed must not carry personal data: {reason}. '
                           f'Nothing was stored.'}, status=422)

        digest = hashlib.sha256(
            json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()

        # Snapshot-replace: the newest push for a dataset is the truth.
        # An identical re-send is not fresh data. Say so rather than pretending.
        existing = GraphiteSnapshot.objects.filter(dataset=dataset).first()
        if existing is not None and existing.content_hash == digest:
            logger.info('graphite-ingest unchanged dataset=%s rows=%s', dataset, len(rows))
            return Response({
                'received': True, 'dataset': dataset, 'rows': len(rows),
                'replaced': False, 'unchanged': True,
                'note': 'Identical to the snapshot already held — nothing changed.',
            }, status=200)

        snap, created = GraphiteSnapshot.objects.update_or_create(
            dataset=dataset,
            defaults={'payload': {'rows': rows}, 'row_count': len(rows),
                      'content_hash': digest, 'source_ip': _client_ip(request)[:45]},
        )
        logger.info('graphite-ingest stored dataset=%s rows=%s %s ip=%s',
                    dataset, len(rows), 'created' if created else 'replaced',
                    _client_ip(request) or '?')
        return Response({
            'received': True,
            'dataset': dataset,
            'rows': len(rows),
            'replaced': not created,
            'unchanged': False,
            'note': 'Stored. Nothing in Omni acts on this automatically.',
        }, status=201 if created else 200)
