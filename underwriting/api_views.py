"""
underwriting/api_views.py

Endpoints (all under /api/v1/underwriting/):
  GET  documents/            register of issued/draft documents (company-scoped)
  GET  documents/{id}/pdf/   download a stored PDF (company-scoped — no IDOR)
  POST render/               {doctype, fmt, fields} -> A4 PDF bytes (preview/download)
  POST issue/                render + store + audit (+ optional email) -> record
  POST extract/              smart reader (DeepSeek->Gemini); throttled + size-capped
"""
from __future__ import annotations

import logging
import re
from collections import Counter

log = logging.getLogger(__name__)

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.throttling import ScopedRateThrottle

from core.mixins import (CompanyScopedViewSetMixin, resolve_company_id_param,
                         scoped_company_ids)
from .models import Quote, QuoteRateFloor, QuoteTemplate, UnderwritingDocument
from .serializers import QuoteSerializer, UnderwritingDocumentSerializer
from .render import render_pdf, RenderUnavailable
from .reader import extract_fields
from .quote_parse import draft_quote, premium_appears_in
from .quote_render import render_quote_pdf

# Max upload the reader will accept (post-decode). Broker instructions are a
# few hundred KB; this bounds the memory + external-API cost per call.
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _company_from_body(data) -> str | None:
    """Resolve an explicit company (UUID or code) from a POST body — a POST
    carries no ?company= auto-injection, so the topbar selection rides the body."""
    raw = str((data or {}).get('company') or '').strip()
    if not raw:
        return None
    from core.models import Company
    from django.db.models import Q
    try:
        row = Company.objects.filter(Q(pk=raw)).first()
    except Exception:   # noqa: BLE001 — non-UUID string
        row = None
    if row is None:
        row = Company.objects.filter(code__iexact=raw).first()
    return str(row.pk) if row else None

_SUBJECTS = {
    'wca':  "Alpha Direct — Worker's Compensation Certificate",
    'cn':   'Alpha Direct — Cover Note',
    'cnfi': 'Alpha Direct — Cover Note',
}
_FILENAMES = {'wca': 'WCA-Certificate', 'cn': 'Cover-Note', 'cnfi': 'Cover-Note'}

# ---- parse-text (phone flow) ------------------------------------------------
_MAX_PARSE_CHARS = 8000
_PARSE_DOCTYPES = ('quote', 'cn', 'cnfi', 'wca')

# Alpha's own signatory, verbatim from templates/underwriting/tool.html. A cover
# note is a binding document: the name on it comes from HERE, never from the
# sentence and never from a model (Fable audit 2026-07-08).
_DEFAULT_SIGNATORY = {
    'cn_signame':  'Gaolebale S. Machobane',
    'cn_sigphone': '+267 3702714',
    'cn_sigemail': 'gmachobane@alphadirect.co.bw',
}
_DEFAULT_TERRITORY = ('Loss must occur in the Republic of Botswana, Namibia, Lesotho, '
                      'South Africa, Malawi, Zimbabwe and Swaziland.')

# Two passes. A word that NAMES the document wins ("cover note", "WCA",
# "quotation"); only if nothing names it do the weaker cues decide ("premium",
# "employees", "bank"). Otherwise "WCA certificate … premium 4,500" would come
# back as a quotation because 'premium' was matched first.
_STRONG = (
    ('quote', re.compile(r'\bquot(?:e|es|ation|ations)\b', re.I)),
    ('wca',   re.compile(r"\bworkm[ae]n'?s?\b|\bWCA\b|\bcompensation\b", re.I)),
    ('cnfi',  re.compile(r'\bcover\s*note\b.*\b(?:financed?|financier|bank|hire\s+purchase)\b'
                         r'|\b(?:financed?|financier|bank|hire\s+purchase)\b.*\bcover\s*note\b', re.I | re.S)),
    ('cn',    re.compile(r'\bcover\s*note\b', re.I)),
)
_WEAK = (
    ('quote', re.compile(r'\bpremium\b|\brate\b', re.I)),
    ('wca',   re.compile(r'\bemployees?\b|\bearnings\b', re.I)),
    ('cnfi',  re.compile(r'\bfinanced?\b|\bfinancier\b|\bbank\b|hire\s+purchase', re.I)),
    ('cn',    re.compile(r'\bcover\b|\bpolicy\b', re.I)),
)


def _detect_doctype(text: str) -> str:
    for dt, rx in _STRONG:
        if rx.search(text):
            return dt
    for dt, rx in _WEAK:
        if rx.search(text):
            return dt
    return ''


def _fold_cover_family(target: str, f: dict) -> dict:
    """Map between the plain cover note (cn_*) and the financed one (cn_* + fi_*).
    Into 'cn': the vehicle class/value/make+reg become class/sum insured/risk.
    Into 'cnfi': cn_* survive, fi_* stay blank for the underwriter to complete."""
    out = {k: v for k, v in f.items() if k.startswith('cn_')}
    if target == 'cn':
        out['cn_class'] = out.get('cn_class') or f.get('fi_class', '')
        out['cn_sum'] = out.get('cn_sum') or f.get('fi_value', '')
        risk = ' '.join(x for x in (f.get('fi_make', ''), f.get('fi_reg', '')) if x)
        out['cn_risk'] = out.get('cn_risk') or risk
        return out
    for k in ('fi_class', 'fi_reg', 'fi_make', 'fi_value', 'fi_bank'):
        out[k] = f.get(k, '')
    return out


def _parse_certificate(text: str, doctype: str) -> dict:
    """Tier 0 rules → (only if not confident) the reader's firewalled AI path.
    Returns the blank field set with a warning rather than failing, so the phone
    always lands on the editable form."""
    from .reader import FIELDS, _finalize, _map_text
    from .extract_rules import rule_extract

    warnings: list[str] = []
    rules = rule_extract(text)
    fields: dict = {}
    via = ''
    ok = False
    # The rules guess their own doctype from the words; the caller's (or the
    # router's) decision wins, so a WCA sentence read as 'cn' by the regex still
    # lands on the WCA form.
    if rules.get('ok') and rules.get('doctype') == doctype:
        done = _finalize(doctype, rules['fields'], 'rules')
        if done:
            fields, via, ok = done['fields'], 'rules', True
    if not ok:
        parsed, ai_via = _map_text(text)
        # cn and cnfi are ONE family: a vehicle cover note with no financier is
        # read by the model as 'cnfi' (it sees reg/make/value) although the
        # caller asked for a plain 'cn'. Accept the family and fold the vehicle
        # fields into the plain cover note instead of throwing a good read away
        # (4-Sep-2026: the first live sentence fell back to the regex tier).
        if parsed and parsed.get('doctype') != doctype \
                and {parsed.get('doctype'), doctype} <= {'cn', 'cnfi'}:
            parsed = {'doctype': doctype,
                      'fields': _fold_cover_family(doctype, parsed.get('fields') or {})}
        if parsed and parsed.get('doctype') == doctype:
            done = _finalize(doctype, parsed['fields'], ai_via)
            if done:
                fields, via, ok = done['fields'], ai_via, True
        elif parsed:
            warnings.append(f'Aria read this as a {parsed.get("doctype")} — kept as '
                            f'{doctype} because that is what you asked for. Check every field.')
    if not ok and rules.get('doctype') == doctype and any((rules.get('fields') or {}).values()):
        done = _finalize(doctype, rules['fields'], 'rules (partial)')
        if done:
            fields, via = done['fields'], 'rules (partial)'
            warnings.append('Only some fields could be read — fill in the rest.')
    if not fields:
        fields = {k: '' for k in FIELDS[doctype]}
        warnings.append('Could not read the details from that sentence — fill the form in by hand.')
    if doctype in ('cn', 'cnfi'):
        if not fields.get('cn_terr'):
            fields['cn_terr'] = _DEFAULT_TERRITORY
        fields.update(_DEFAULT_SIGNATORY)           # forced, whatever the text said
    return {'ok': ok, 'doctype': doctype, 'fields': fields,
            'confidence': rules.get('confidence', 0.0) if via.startswith('rules') else (1.0 if ok else 0.0),
            'via': via, 'warnings': warnings}


def _anchors(doctype: str, fields: dict) -> tuple[str, str]:
    """(policy_number, insured_name) pulled from the payload for the register."""
    f = fields or {}
    if doctype == 'wca':
        return (f.get('w_policy', ''), f.get('w_name', ''))
    return (f.get('cn_policy', ''), f.get('cn_client', ''))


class UnderwritingDocumentViewSet(CompanyScopedViewSetMixin,
                                   mixins.ListModelMixin,
                                   mixins.RetrieveModelMixin,
                                   viewsets.GenericViewSet):
    # Entity scoping (Fable audit 2026-07-08): inherit the mixin so the register
    # AND the detail /pdf action honour UserCompanyAccess — closes the IDOR that
    # let any authenticated user pull any entity's stored certificate by id.
    company_lookup_field = 'company_id'
    queryset = UnderwritingDocument.objects.select_related(
        'company', 'issued_by').all()
    serializer_class = UnderwritingDocumentSerializer
    parser_classes = [JSONParser, MultiPartParser]
    throttle_scope = 'underwriting'

    def get_throttles(self):
        # Rate-limit the expensive actions (paid LLM / headless render); leave
        # plain list/retrieve unthrottled.
        if getattr(self, 'action', None) in ('extract', 'render', 'issue', 'parse_text'):
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        qs = super().get_queryset()      # company-scoped by the mixin
        dt = self.request.query_params.get('doctype')
        if dt:
            qs = qs.filter(doctype=dt)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    # ---- render (no persistence) -------------------------------------------
    @action(detail=False, methods=['post'], url_path='render')
    def render(self, request):
        d = request.data
        try:
            pdf = render_pdf(d.get('doctype', 'wca'), d.get('fmt', 'orig'),
                             d.get('fields', {}))
        except RenderUnavailable as e:
            return Response(
                {'detail': 'Server PDF render is unavailable — use "Save PDF" '
                           'in the tool (your browser prints it). ' + str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        resp = HttpResponse(pdf, content_type='application/pdf')
        fn = _FILENAMES.get(d.get('doctype'), 'Document')
        resp['Content-Disposition'] = f'inline; filename="{fn}.pdf"'
        return resp

    # ---- issue (render + store + audit + optional email) -------------------
    @action(detail=False, methods=['post'], url_path='issue')
    def issue(self, request):
        d = request.data
        doctype = d.get('doctype', 'wca')
        fmt = d.get('fmt', 'orig') if doctype == 'wca' else ''
        fields = d.get('fields', {}) or {}
        email_to = (d.get('email_to') or '').strip()

        try:
            pdf = render_pdf(doctype, fmt, fields)
        except RenderUnavailable as e:
            return Response(
                {'detail': 'Server render unavailable — cannot store/email yet. '
                           'Use "Save PDF" in the tool meanwhile. ' + str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        policy, insured = _anchors(doctype, fields)
        # Prefer the topbar company sent in the body (a POST carries no
        # ?company= auto-inject) — else header/query/profile. Without this the
        # doc is tagged to the profile default and vanishes from the entity
        # register (Fable audit 2026-07-08).
        company_id = _company_from_body(d) or resolve_company_id_param(request)
        # Same clamp as the quote create path: the body and the query string are
        # both caller-supplied, so a resolved company is not an allowed one.
        allowed = scoped_company_ids(request)
        if (company_id and allowed is not None
                and str(company_id) not in {str(c) for c in allowed}):
            return Response(
                {'detail': 'You cannot issue a document against that company.'},
                status=status.HTTP_403_FORBIDDEN)
        doc = UnderwritingDocument(
            doctype=doctype, fmt=fmt, fields=fields,
            policy_number=policy[:60], insured_name=insured[:200],
            company_id=company_id,
            status=UnderwritingDocument.Status.ISSUED,
            pdf_bytes=pdf, pdf_size=len(pdf),
            issued_by=request.user, issued_at=timezone.now(),
        )
        doc.save(audit_user=request.user)

        emailed = False
        email_error = None
        if email_to:
            try:
                self._email_document(doc, email_to, request.user)
                emailed = True
            except Exception as e:  # noqa: BLE001 — email failure must not lose the issued doc
                email_error = str(e)[:200]

        data = UnderwritingDocumentSerializer(doc, context={'request': request}).data
        data['emailed'] = emailed
        if email_error:
            data['email_error'] = email_error
        return Response(data, status=status.HTTP_201_CREATED)

    def _email_document(self, doc, to_addr, user):
        from core.notifications import send_html_with_cfo_cc
        subject = _SUBJECTS.get(doc.doctype, 'Alpha Direct — Certificate')
        fn = f"{_FILENAMES.get(doc.doctype, 'Document')}-{doc.policy_number or doc.id}.pdf"
        html = (
            '<p>Dumela,</p>'
            f'<p>Please find attached your {doc.get_doctype_display()} from '
            'Alpha Direct Insurance.</p>'
            '<p>Regards,<br/>Alpha Direct Insurance</p>')
        # cc_cfo=False: these go to external brokers/clients — do NOT cc the
        # internal excoboard@ (it would leak the internal address to every
        # recipient and spam EXCO on every cert). Fable audit 2026-07-08.
        n = send_html_with_cfo_cc(
            subject, html, to=[to_addr],
            text_fallback='Please find your document attached.',
            attachments=[(fn, bytes(doc.pdf_bytes), 'application/pdf')],
            cc_cfo=False,
        )
        if not n:
            raise RuntimeError('mailer returned 0 (not sent)')
        doc.emailed_to = to_addr[:254]
        doc.emailed_at = timezone.now()
        doc.save(audit_user=user, update_fields=['emailed_to', 'emailed_at', 'updated_at'])

    # ---- short-lived token to open the (signature-bearing) tool page --------
    @action(detail=False, methods=['get'], url_path='tool-token')
    def tool_token(self, request):
        """Mint a 15-min signed token so the authed parent can load the tool
        iframe. The tool page embeds the real signature/stamp, so it is not
        openable without this (anti-forgery — Fable audit 2026-07-08)."""
        from django.core import signing
        from .views import TOOL_TOKEN_SALT
        token = signing.dumps({'u': request.user.id}, salt=TOOL_TOKEN_SALT)
        return Response({'token': token})

    # ---- stored PDF download ------------------------------------------------
    @action(detail=True, methods=['get'], url_path='pdf')
    def pdf(self, request, pk=None):
        doc = self.get_object()
        if not doc.pdf_bytes:
            return Response({'detail': 'No stored PDF for this document.'},
                            status=status.HTTP_404_NOT_FOUND)
        resp = HttpResponse(bytes(doc.pdf_bytes), content_type='application/pdf')
        fn = f"{_FILENAMES.get(doc.doctype, 'Document')}-{doc.policy_number or doc.id}.pdf"
        resp['Content-Disposition'] = f'inline; filename="{fn}"'
        return resp

    # ---- plain English → the right document (phone flow, CFO 2026-09-04) ----
    @action(detail=False, methods=['post'], url_path='parse-text')
    def parse_text(self, request):
        """One sentence typed on the phone → {doctype, fields} for a cover
        note / financed cover note / WCA certificate, or the quotation draft.

        Same engines as the desktop, nothing new: quotes go to
        quote_parse.draft_quote (money computed in code); certificates run
        the free regex tier first and only escalate to reader._map_text — the
        PII-firewalled DeepSeek→Gemini path the FILE reader uses — when the
        rules are not confident. The signatory is never read from the text.
        """
        d = request.data or {}
        text = str(d.get('text') or '').strip()
        if not text:
            return Response({'detail': 'Describe what you need first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(text) > _MAX_PARSE_CHARS:
            return Response({'detail': 'That description is too long — trim it to 8,000 characters.'},
                            status=status.HTTP_400_BAD_REQUEST)
        wanted = str(d.get('doctype') or 'auto').strip().lower()
        if wanted not in _PARSE_DOCTYPES and wanted != 'auto':
            return Response({'detail': 'doctype must be auto, quote, cn, cnfi or wca.'},
                            status=status.HTTP_400_BAD_REQUEST)
        doctype = wanted if wanted != 'auto' else _detect_doctype(text)
        if not doctype:
            return Response({'ok': False, 'needs_doctype': True,
                             'options': list(_PARSE_DOCTYPES),
                             'detail': 'Which document is this — a quote, a cover note, '
                                       'a financed cover note or a WCA certificate?'})
        if doctype == 'quote':
            result = draft_quote(text)
            money = result['money']
            return Response({
                'ok': result['ok'], 'doctype': 'quote', 'source': result['source'],
                'draft': result['draft'],
                'premium': str(money['premium']), 'vat': str(money['vat']),
                'vat_rate_pct': money['vat_rate_pct'], 'total': str(money['total']),
                'premium_is_suggested': result.get('premium_is_suggested', False),
                'premium_basis': result.get('premium_basis', ''),
                'warnings': result['warnings'],
            })
        try:
            return Response(_parse_certificate(text, doctype))
        except Exception as exc:  # the form must still open — never a raw 500 to the phone
            log.warning('parse-text certificate path failed: %s', type(exc).__name__)
            return Response({'ok': False, 'doctype': doctype, 'fields': {}, 'confidence': 0,
                             'via': 'error', 'warnings': ['Omni could not read that. Fill the form by hand.']})

    # ---- smart reader (DeepSeek → Gemini) ----------------------------------
    @action(detail=False, methods=['post'], url_path='extract')
    def extract(self, request):
        # Multipart (direct) OR base64 JSON (parent-proxied from the iframe so
        # the authed call is made by the Omni page, not the frame).
        f = request.FILES.get('file')
        if f:
            raw = f.read()
            if len(raw) > _MAX_UPLOAD_BYTES:
                return Response({'ok': False, 'message': 'File too large (max 8 MB). '
                                 'Crop/compress the scan or fill the form manually.'})
            return Response(extract_fields(raw, f.name, f.content_type or ''))
        b64 = (request.data or {}).get('file_b64')
        if b64:
            import base64 as _b64
            try:
                raw = _b64.b64decode(str(b64).split(',', 1)[-1])
            except Exception:  # noqa: BLE001
                return Response({'ok': False, 'message': 'Could not read the file.'})
            if len(raw) > _MAX_UPLOAD_BYTES:
                return Response({'ok': False, 'message': 'File too large (max 8 MB). '
                                 'Crop/compress the scan or fill the form manually.'})
            return Response(extract_fields(
                raw, request.data.get('filename') or 'upload',
                request.data.get('content_type') or ''))
        return Response({'ok': False, 'message': 'No file uploaded.'})


class QuoteViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    """Quotations — the register, plus the ask box that fills one in.

      GET    quotes/                 the register (company-scoped)
      POST   quotes/                 create a draft
      PATCH  quotes/{id}/            the underwriter corrects the draft
      POST   quotes/draft-from-text/ ask box: plain English -> a filled draft
      POST   quotes/{id}/confirm-premium/  accept a premium Aria suggested
      POST   quotes/{id}/issue/      number it, freeze the PDF, register it
      POST   quotes/{id}/outcome/    won / lost — conversion tracking
      GET    quotes/{id}/pdf/        the stored copy
    """
    queryset = Quote.objects.select_related('company', 'underwriter', 'issued_by')
    serializer_class = QuoteSerializer
    # 'uw_extract' is not a configured rate — using it made DRF raise
    # ImproperlyConfigured inside check_throttles, so EVERY request 500'd,
    # including plain list. Share the reader's real scope, and only spend it on
    # the expensive actions, exactly as the sibling viewset does.
    throttle_scope = 'underwriting'

    def get_throttles(self):
        # Customer-facing send gets its own, tighter bucket (security review
        # 2026-09-04): 6/min per staff account, separate from drafting/issuing.
        if getattr(self, 'action', None) == 'email':
            t = ScopedRateThrottle(); t.scope = 'underwriting-email'
            return [t]
        # from_policy hits the Graphite replica and reads across entities, so
        # rate-limit it against policy-number enumeration alongside the LLM paths.
        if getattr(self, 'action', None) in ('draft_from_text', 'issue', 'from_policy', 'email'):
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        """The entity is taken from the caller, never from the payload.

        DeepSeek review 2026-08-08: perform_create only set the underwriter, so a
        quote could be saved with no company at all — invisible to every
        per-entity view — or against somebody else's entity.

        `premium_is_suggested` is read-only on the serializer so a PATCH cannot
        clear it, but the ask box has to be able to RAISE it at creation. It is
        honoured here, on create only, and from then on nothing but
        confirm-premium can lower it.
        """
        # Resolved from the authenticated caller / topbar selection ONLY. The
        # body is deliberately not consulted: a payload-supplied entity is how a
        # quote ends up on another company's books.
        company_id = resolve_company_id_param(self.request)
        if not company_id:
            raise DRFValidationError(
                'Pick the company at the top of the screen before saving a quotation. '
                'A quotation with no entity is invisible to every per-company view.')

        # Resolved is not the same as allowed. The line above returns the company
        # the caller ASKED for — it comes off the query string, so anyone can name
        # an entity they cannot see. Read-side scoping does not cover a write:
        # without this clamp a restricted user can save a quotation onto another
        # company's books, which is exactly the wall closed on 8 Aug 2026.
        allowed = scoped_company_ids(self.request)
        if allowed is not None and str(company_id) not in {str(c) for c in allowed}:
            raise DRFValidationError(
                'You cannot save a quotation against that company. '
                'Pick one of your own entities at the top of the screen.')

        # Whether the premium was a guess is DERIVED here, not taken on trust. The
        # client used to send the flag, which meant simply omitting it saved an
        # AI-suggested premium as though the underwriter had confirmed it — the
        # gate the CFO asked for, bypassed by leaving a field out.
        #
        # The server can settle it from data it can check: if the note the
        # underwriter typed does not contain the premium figure, then the figure
        # did not come from them.
        # Raise-only. Deriving it from the note alone treats absent evidence as
        # innocence: draft from a note, let Aria suggest a premium, then clear the
        # text box before saving, and an empty source_text derives "not suggested"
        # — the guess issues as though somebody had confirmed it. The client may
        # RAISE the flag, never lower it.
        source_text = str((self.request.data or {}).get('source_text') or '')
        suggested = (
            (bool(source_text) and not premium_appears_in(
                serializer.validated_data.get('premium'), source_text))
            or bool((self.request.data or {}).get('premium_is_suggested'))
        )

        serializer.save(
            underwriter=self.request.user,
            company_id=company_id,
            premium_is_suggested=suggested,
            drafted_by_ai=bool(source_text),
        )

    def perform_update(self, serializer):
        """The underwriter may correct anything on a DRAFT. Once issued the
        quotation is what the client was sent — it is not edited, it is
        superseded by a new version."""
        if serializer.instance.status != Quote.Status.DRAFT:
            raise DRFValidationError(
                'This quotation has been issued. Raise a new version instead of '
                'changing what the client was sent.')
        # Typing a premium by hand IS the confirmation — but only when the figure
        # actually changes. Re-sending Aria's own number back is not somebody
        # looking at it and agreeing; that would clear the gate by accident.
        # Accepting the suggested figure unchanged is what confirm-premium is for.
        new_premium = serializer.validated_data.get('premium')
        if new_premium is not None and new_premium != serializer.instance.premium:
            serializer.save(premium_is_suggested=False)
        else:
            serializer.save()

    def perform_destroy(self, instance):
        """An issued quotation is the record of what a client was sent. The
        register is worthless if rows can disappear from it."""
        if instance.status != Quote.Status.DRAFT:
            raise DRFValidationError(
                'An issued quotation cannot be deleted — it is the record of what '
                'the client was sent.')
        instance.delete()

    # ---- the ask box --------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='draft-from-text')
    def draft_from_text(self, request):
        """Plain English in, a filled quotation draft out. Nothing is saved —
        the screen shows it, the underwriter checks it, then saves."""
        text = str((request.data or {}).get('text') or '').strip()
        if not text:
            return Response({'ok': False, 'message': 'Type the quote details first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(text) > 8000:
            return Response({'ok': False, 'message': 'That note is too long — trim it.'},
                            status=status.HTTP_400_BAD_REQUEST)
        result = draft_quote(text)
        money = result['money']
        return Response({
            'ok': result['ok'],
            'source': result['source'],
            'draft': result['draft'],
            'premium': str(money['premium']),
            'vat': str(money['vat']),
            'vat_rate_pct': money['vat_rate_pct'],
            'total': str(money['total']),
            'premium_is_suggested': result.get('premium_is_suggested', False),
            'premium_basis': result.get('premium_basis', ''),
            'warnings': result['warnings'],
        })

    @action(detail=True, methods=['post'], url_path='confirm-premium')
    def confirm_premium(self, request, pk=None):
        """The underwriter has looked at Aria's suggested figure and accepts it
        (optionally replacing it). Until this happens the quotation cannot issue."""
        quote = self.get_object()
        if quote.status != Quote.Status.DRAFT:
            # Otherwise the premium could be changed after issue while the stored
            # PDF — the thing the client actually holds — keeps the old figures.
            return Response(
                {'detail': 'This quotation has been issued. Its premium cannot be changed.'},
                status=status.HTTP_400_BAD_REQUEST)
        typed = (request.data or {}).get('premium')
        if typed not in (None, ''):
            from .quote_parse import _to_decimal
            value = _to_decimal(typed)
            if value is None or value <= 0:
                return Response({'detail': 'That premium is not a number.'},
                                status=status.HTTP_400_BAD_REQUEST)
            quote.premium = value
        if not quote.premium or quote.premium <= 0:
            return Response({'detail': 'Type a premium before confirming.'},
                            status=status.HTTP_400_BAD_REQUEST)
        quote.premium_is_suggested = False
        quote.save()
        return Response(QuoteSerializer(quote).data)

    @action(detail=False, methods=['get'], url_path='from-policy')
    def from_policy(self, request):
        """Renewal helper: read a Graphite policy by number to pre-fill the quote
        (client, class, prior premium, sum insured) and surface the client's
        claims. Policy-number keyed — no name matching. Only non-personal fields
        are returned; the Graphite read runs behind the PII-blocking guard.

        Internal underwriting use only: the caller must have access to at least
        one company (a real staff member with entity access), and the action is
        throttled — this reads across entities, so it cannot be left open to any
        authenticated account to enumerate policies by number."""
        if not scoped_company_ids(request):
            return Response({'found': False, 'error': 'Not permitted.'},
                            status=status.HTTP_403_FORBIDDEN)
        policy = (request.query_params.get('policy') or '').strip()
        if not policy:
            return Response({'found': False, 'error': 'Enter a policy number.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            from .renewal_lookup import lookup_policy
            return Response(lookup_policy(policy))
        except Exception:                       # noqa: BLE001
            log.exception('quote renewal lookup failed')   # no raw input logged
            return Response(
                {'found': False, 'error': 'The renewal lookup is unavailable right now.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE)

    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None):
        """Download the quotation as Excel or Word (?fmt=xlsx|docx).

        The PDF stays on its own action — it is the document a client is SENT.
        These two are working copies; the Excel is live (every money cell is a
        formula) so an underwriter can re-price without retyping.

        The chooser is `fmt`, NOT `format`: `format` is DRF's reserved content-
        negotiation query param (URL_FORMAT_OVERRIDE), so `?format=xlsx` never
        reached this code — DRF looked for an 'xlsx' RENDERER, found none, and
        returned 404 first. Every Excel/Word download 404'd; only the PDF (its own
        `/pdf/` action, no query param) survived. Proven on prod 2026-08-12
        (Motlatsi item 2): ?format=xlsx → 404, ?fmt=xlsx → 200.
        """
        quote = self.get_object()
        fmt = (request.query_params.get('fmt') or 'xlsx').lower()
        # detailed (default) or simple — the short version with the premium table
        # but not the row-by-row schedule or the exclusions (CFO 2026-08-11).
        style = (request.query_params.get('style') or 'detailed').lower()
        from .quote_export import build_docx, build_xlsx, filename_for
        if fmt in ('xlsx', 'excel'):
            data, ext, ctype = build_xlsx(quote), 'xlsx', (
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        elif fmt in ('docx', 'word'):
            data, ext, ctype = build_docx(quote), 'docx', (
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        elif fmt == 'pdf':
            from .quote_render import render_quote_pdf
            try:
                data = render_quote_pdf(quote, style=style)
            except RenderUnavailable as exc:
                return Response({'detail': str(exc)},
                                status=status.HTTP_503_SERVICE_UNAVAILABLE)
            ext, ctype = 'pdf', 'application/pdf'
        else:
            return Response({'detail': 'format must be pdf, xlsx or docx.'},
                            status=status.HTTP_400_BAD_REQUEST)
        resp = HttpResponse(data, content_type=ctype)
        resp['Content-Disposition'] = f'attachment; filename="{filename_for(quote, ext)}"'
        return resp

    @action(detail=False, methods=['get'], url_path='exclusions')
    def exclusions(self, request):
        """The exclusions most used for a class of business — LEARNED from the
        quotes we have already issued (CFO 2026-08-10).

        No model, no curation: count the lines underwriters actually typed, most
        used first, so the wording converges on itself instead of being retyped.
        Scoped to the same class where we have history, and falls back to the
        commonest across all classes for a brand-new class. Exact class match
        first (case-insensitive) so a Fire quote is not seeded with motor wording.
        """
        cls = (request.query_params.get('class') or '').strip()
        qs = Quote.objects.exclude(exclusions=[]).values_list('class_of_business', 'exclusions')
        same, other = Counter(), Counter()
        for cob, lines in qs:
            bucket = same if cls and (cob or '').strip().lower() == cls.lower() else other
            for line in (lines or []):
                text = str(line).strip()
                if text:
                    bucket[text] += 1
        ranked = same.most_common(12) or other.most_common(12)
        return Response([{'text': t, 'used': n} for t, n in ranked])

    @action(detail=False, methods=['get'], url_path='templates')
    def templates(self, request):
        """Active quote templates — a starting point for a class of business
        (standard cover rows + a default rate) so the screen isn't a blank page."""
        rows = (QuoteTemplate.objects.filter(active=True)
                .values('id', 'name', 'class_of_business', 'sections', 'rate_pct', 'rate_incl_vat'))
        return Response([{
            'id': r['id'], 'name': r['name'],
            'class_of_business': r['class_of_business'],
            'sections': r['sections'] or [],
            'rate_pct': str(r['rate_pct']),
            'rate_incl_vat': r['rate_incl_vat'],
        } for r in rows])

    @action(detail=False, methods=['get'], url_path='rate-floors')
    def rate_floors(self, request):
        """The active minimum-rate floors, so the quote screen can warn live when
        a rate is below the floor for its class (the hard block is at issue())."""
        rows = (QuoteRateFloor.objects.filter(active=True)
                .values('class_of_business', 'min_rate_pct'))
        return Response([
            {'class_of_business': r['class_of_business'], 'min_rate_pct': str(r['min_rate_pct'])}
            for r in rows
        ])

    @action(detail=False, methods=['get'], url_path='adoption')
    def adoption(self, request):
        """Who is USING the underwriting automation, and who is still manual.

        CFO Amendment 3, 2026-09-08. Feeds /underwriting/adoption — the screen
        management looks at. Derived entirely from rows the two tools already
        write (see underwriting/adoption.py for why there is no event table),
        plus the renewal count from Graphite over the existing read-only bridge.

        Deliberately NOT company-scoped: a person's adoption is a person's
        adoption, and splitting it per entity would hide half of each
        underwriter's work behind the topbar selection.
        """
        from .adoption import weekly_report

        try:
            days = int(request.query_params.get('days') or 7)
        except (TypeError, ValueError):   # junk ?days= -> the default window
            days = 7
        days = max(1, min(days, 365))

        rep = weekly_report(days=days)
        iso = lambda d: d.isoformat() if d else None
        return Response({
            'window': {
                'start': iso(rep['window']['start']),
                'end':   iso(rep['window']['end']),
                'days':  rep['window']['days'],
            },
            'team': rep['team'],
            'rows': [{
                'user_id':   r['user_id'],
                'name':      r['name'],
                'job_title': r['job_title'],
                'is_manager': r['is_manager'],
                'quotes':    r['quotes'],
                'quotes_last_used': iso(r['quotes_last_used']),
                'documents': r['documents'],
                'documents_last_used': iso(r['documents_last_used']),
                'ever_used': r['ever_used'],
                'renewals':  r['renewals'],
                'gap':       r['gap'],
                'readiness': r['readiness'],
                'readiness_band': r['readiness_band'],
            } for r in rep['rows']],
        })

    @action(detail=True, methods=['post'])
    def issue(self, request, pk=None):
        quote = self.get_object()
        # The whole issue — status change, render, document gate — is ONE
        # transaction: if the printed document fails its own checks the issue
        # rolls back entirely and the quote stays a draft with the reasons on
        # screen. Built after two print faults reached the CFO on 11 Aug 2026;
        # the document now measures itself every time, so a change in a quote's
        # SIZE (a 15-vehicle fleet, a 40-row schedule) cannot quietly break the
        # layout again. See quote_doc_gate.
        from django.db import transaction
        from .quote_doc_gate import blocking_findings, advisory_findings

        class _DocumentBlocked(Exception):
            pass

        advisories: list[str] = []
        try:
            with transaction.atomic():
                quote.issue(request.user)
                try:
                    pdf = render_quote_pdf(quote)
                except Exception:                   # noqa: BLE001
                    # No renderer, no document to check. The quotation is
                    # issued and numbered; the copy is produced on demand by
                    # the pdf action below — the long-standing behaviour.
                    pdf = None
                if pdf is not None:
                    # HARD block only on the deterministic faults (geometry +
                    # exact-duplicate lines): they are measured, not judged, so
                    # they cannot be a false alarm. Aria's read is gathered as
                    # ADVISORY warnings — it catches real things but also
                    # misreads, so it must never veto a correct quote.
                    problems = blocking_findings(pdf, quote.sections)
                    if problems:
                        raise _DocumentBlocked(problems)
                    advisories = advisory_findings(pdf)
                    quote.pdf_bytes, quote.pdf_size = pdf, len(pdf)
                    Quote.objects.filter(pk=quote.pk).update(
                        pdf_bytes=quote.pdf_bytes, pdf_size=quote.pdf_size)
        except DjangoValidationError as exc:
            return Response({'detail': ' '.join(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST)
        except _DocumentBlocked as exc:
            problems = exc.args[0]
            quote.refresh_from_db()
            return Response(
                {'detail': 'The document failed its own print check, so this '
                           'quotation has NOT been issued. Fix the schedule and '
                           'try again.',
                 'problems': problems},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        data = QuoteSerializer(quote).data
        if not quote.pdf_size:
            data['pdf_missing'] = True
            data['pdf_message'] = ('The quotation is issued and numbered, but the '
                                   'stored copy could not be produced. Open the PDF '
                                   'to generate it.')
        # Aria's advisory notes: the quotation IS issued; these are worth a
        # glance but did not stop it (they may be misreads).
        if advisories:
            data['advisories'] = advisories
        return Response(data)

    @action(detail=True, methods=['post'])
    def outcome(self, request, pk=None):
        """Won or lost — this is what makes the register worth keeping."""
        quote = self.get_object()
        result = str((request.data or {}).get('result') or '').lower().strip()
        if result not in ('won', 'lost'):
            return Response({'detail': 'Result must be won or lost.'},
                            status=status.HTTP_400_BAD_REQUEST)
        # WON and LOST are correctable between themselves. A mis-click on the
        # wrong row was permanent — status is read-only on the serializer, so not
        # even a PATCH could undo it — and it silently skewed the conversion rate
        # the register exists to report. A draft still has no outcome.
        correctable = (Quote.Status.ISSUED, Quote.Status.LAPSED,
                       Quote.Status.WON, Quote.Status.LOST)
        if quote.status not in correctable:
            return Response({'detail': 'Only an issued quotation has an outcome.'},
                            status=status.HTTP_400_BAD_REQUEST)
        quote.status = Quote.Status.WON if result == 'won' else Quote.Status.LOST
        quote.converted_policy_number = str(
            (request.data or {}).get('policy_number') or '').strip()[:60]
        quote.outcome_at = timezone.now()
        quote.save()
        return Response(QuoteSerializer(quote).data)

    @action(detail=True, methods=['post'])
    def email(self, request, pk=None):
        """Send the ISSUED quotation's PDF to the client from Omni (phone flow,
        CFO 2026-09-04). Customer mail: no EXCO copy, no do-not-reply banner
        (external recipient ⇒ the mailer leaves it off). A draft is refused —
        what the client holds must be the numbered, frozen document."""
        from django.core.validators import validate_email
        from core.notifications import send_html_with_cfo_cc, wrap_plain_as_html

        quote = self.get_object()
        to = str((request.data or {}).get('to') or '').strip()
        try:
            validate_email(to)
        except DjangoValidationError:
            return Response({'detail': 'Enter a valid email address for the client.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if quote.status == Quote.Status.DRAFT:
            return Response(
                {'detail': 'This quotation is still a draft. Issue it first, then send it.'},
                status=status.HTTP_409_CONFLICT)
        pdf = self._stored_or_rendered_pdf(quote)
        if pdf is None:
            return Response({'detail': 'The quotation PDF could not be produced just now. '
                                       'Try again in a moment.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        valid = quote.valid_until.strftime('%d %B %Y') if quote.valid_until else ''
        agent = (quote.agent or '').strip()
        body = (
            'Dumela,\n\n'
            f'Thank you for considering Alpha Direct Insurance. Attached is your quotation '
            f'{quote.quote_number}'
            + (f' for {quote.class_of_business}' if quote.class_of_business else '') + '.\n\n'
            + (f'It is valid until {valid}.\n\n' if valid else '')
            + 'If you have any questions, please reply to this email'
            + (f' or contact {agent}' if agent else ' or contact your underwriter') + '.\n\n'
            'Regards,\nAlpha Direct Insurance'
        )
        try:
            n = send_html_with_cfo_cc(
                f'Quotation {quote.quote_number} — Alpha Direct Insurance',
                wrap_plain_as_html(body), to=[to],
                text_fallback=body,
                attachments=[(f'Quotation-{quote.quote_number}.pdf', bytes(pdf), 'application/pdf')],
                cc_cfo=False,
            )
            if not n:
                raise RuntimeError('mailer returned 0 (not sent)')
        except Exception as exc:                    # noqa: BLE001 — the quote is untouched; the phone shows why
            log.warning('quote email failed for %s: %s', quote.quote_number, type(exc).__name__)
            return Response({'detail': 'The email could not be sent. '
                                       'Nothing was changed — try again or forward the PDF yourself.'},
                            status=status.HTTP_502_BAD_GATEWAY)
        quote.emailed_to = to[:254]
        quote.emailed_at = timezone.now()
        quote.save(audit_user=request.user, update_fields=['emailed_to', 'emailed_at', 'updated_at'])
        return Response({'emailed': True, 'to': to, 'at': quote.emailed_at})

    def _stored_or_rendered_pdf(self, quote):
        """The frozen copy, or a fresh render stored for next time (the same
        recovery the pdf action does). None when the renderer is down."""
        if quote.pdf_bytes:
            return bytes(quote.pdf_bytes)
        try:
            rendered = render_quote_pdf(quote)
        except Exception:                           # noqa: BLE001 — renderer down; caller answers 503
            return None
        Quote.objects.filter(pk=quote.pk).update(pdf_bytes=rendered, pdf_size=len(rendered))
        quote.pdf_bytes = rendered
        return rendered

    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        quote = self.get_object()
        if not quote.pdf_bytes:
            # Re-render on demand. Without this an issue that hit a busy renderer
            # left the quotation with no copy, permanently, and the promise in the
            # issue() comment that it "can be re-rendered" was not true anywhere.
            if quote.status == Quote.Status.DRAFT:
                return Response({'detail': 'This quotation has not been issued yet.'},
                                status=status.HTTP_404_NOT_FOUND)
            try:
                rendered = render_quote_pdf(quote)
                Quote.objects.filter(pk=quote.pk).update(
                    pdf_bytes=rendered, pdf_size=len(rendered))
                quote.pdf_bytes = rendered
            except Exception:                       # noqa: BLE001
                return Response(
                    {'detail': 'The stored copy could not be produced just now. '
                               'Try again in a moment.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE)
        resp = HttpResponse(bytes(quote.pdf_bytes), content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{quote.quote_number}.pdf"'
        return resp
