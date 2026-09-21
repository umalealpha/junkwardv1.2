"""
nbfira/api_views.py — Phase 2 REST surface (quarterly return only).

Endpoints (all auth-required):

    GET  /api/v1/nbfira/returns/                              list
    POST /api/v1/nbfira/returns/                              create draft
    GET  /api/v1/nbfira/returns/<id>/                         detail + lines
    POST /api/v1/nbfira/returns/<id>/regenerate/              re-run builders
    GET  /api/v1/nbfira/returns/<id>/schedule/<code>/         single schedule
    PATCH /api/v1/nbfira/returns/<id>/lines/<line_id>/         edit one value (draft only)

Phase 4 will add review/approve/lock/submit transitions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

import hashlib
import logging
import os

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import status as drf_status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Company
from core.permissions import CanViewRegulatoryReturns

from .builders import generate_annual, generate_quarterly
from .constants import DEFAULT_MRC, INSURANCE_CLASSES
from .export import build_workbook, file_hash, filename_for
from .filed_parser import parse_a1, parse_a1_inputs
from .models import (
    NBFIRAA1Input, NBFIRAAuditLog, NBFIRACapitalFactor, NBFIRAFiledDocument,
    NBFIRAReturn, NBFIRAReturnLine, NBFIRASubmission,
)
from . import workflow as wf

log = logging.getLogger(__name__)

# What may be attached as a filed return. Deliberately permissive on format:
# this is EVIDENCE storage, not parsing, so an old .xls or a scanned PDF is
# still worth keeping against the period. (A future parsing step may need the
# file re-saved as .xlsx — that is a parsing constraint, not a reason to refuse
# the upload now.)
FILED_DOC_EXTENSIONS = {'.xlsx', '.xlsm', '.xlsb', '.xls', '.csv', '.pdf'}
FILED_DOC_MAX_BYTES  = 25 * 1024 * 1024      # 25 MB
FILED_DOC_STATEMENTS = {'', 'IS', 'A', 'A.1', 'B', 'C'}

# Shown on every A.1 comparison. Omni now computes A.1 the way the filed
# workbook does (CFO decision 2026-08-18, see a1_math). What it still lacks is
# the INPUTS: assumed annual NWP per class, the treaty event retention and the
# asset allocation are all entered figures, not GL-derived, and there is nowhere
# to enter them yet — so Omni falls to the statutory MCR floor. Without this
# note a reader takes the gap for a method dispute or a ledger error; it is
# neither, it is missing inputs.
A1_METHOD_WARNING = (
    "Omni now uses the filed workbook's method — 25% x assumed NWP x (1 + factor) "
    "per class, insurance and market risk combined as a square root of squares, "
    "divided by g-factors derived from the asset mix. Proven against the FY2026 "
    "filed returns for Q1, Q2 and Q4. What Omni does NOT yet have is the inputs: "
    "assumed next-12-month premium by class, the treaty event retention and the "
    "asset allocation are entered figures with nowhere yet to enter them, so "
    "Omni's column falls back to the P5,000k minimum. Read a difference below as "
    "missing inputs, not as a disagreement about the arithmetic."
)


def _audit(return_obj, user, action_, comment='', before=None, after=None, request=None):
    NBFIRAAuditLog.objects.create(
        return_obj=return_obj, user=user, action=action_,
        comment=comment, before_json=before or {}, after_json=after or {},
        ip_address=(request.META.get('REMOTE_ADDR') if request else '') or '',
    )


def _parse_annual_period(label: str) -> tuple[date, date]:
    """Annual label = 'FY<YYYY>' where YYYY is the calendar year of FY end.
    ADIC FY = 1 Jul → 30 Jun, so FY2026 = 2025-07-01 → 2026-06-30.
    """
    label = (label or '').strip().upper()
    if not label.startswith('FY') or len(label) < 5:
        raise ValueError(f'annual period_label must start with FY (got {label!r}).')
    try:
        year = int(label[2:])
    except ValueError as exc:
        raise ValueError(f'annual period_label year: {exc}')
    return date(year - 1, 7, 1), date(year, 6, 30)


def _parse_period(label: str) -> tuple[date, date]:
    """Map a period label to (start, end). Quarterly only for Phase 2.
    Quarterly label = '<YYYY>Q<n>' where n=1..4 maps to:
      Q1 = Jul-Sep, Q2 = Oct-Dec, Q3 = Jan-Mar (next cal year),
      Q4 = Apr-Jun. The YYYY is the calendar year of the END month.
    """
    label = (label or '').strip().upper()
    if 'Q' not in label:
        raise ValueError(f'period_label {label!r} must contain Q')
    year_str, _, q_str = label.partition('Q')
    year = int(year_str)
    q = int(q_str)
    if q == 1:    # Jul-Sep of (year-1)
        return date(year - 1, 7, 1),  date(year - 1, 9, 30)
    if q == 2:    # Oct-Dec of (year-1)
        return date(year - 1, 10, 1), date(year - 1, 12, 31)
    if q == 3:    # Jan-Mar of year
        return date(year, 1, 1), date(year, 3, 31)
    if q == 4:    # Apr-Jun of year
        return date(year, 4, 1), date(year, 6, 30)
    raise ValueError(f'invalid quarter in {label!r}')


class NBFIRAReturnViewSet(viewsets.ViewSet):
    """Phase 2 minimal viewset — read + create draft + regenerate."""

    # Audience gate added 2026-08-17 (Fable 5 + Gemini + OpenAI all flagged
    # H5). Everything here is GL-derived regulatory financial data, and the new
    # filed-document endpoints let a caller DOWNLOAD the company's actual filed
    # NBFIRA returns — IsAuthenticated alone meant any staff login could read
    # them. Gated at CLASS level on purpose: locking only the new endpoints while
    # generate / edit / export stayed open would be incoherent.
    # CFO 2026-08-17: "managers and fc and fm only" — deliberately TIGHTER than
    # CanViewFinancials, which also admits accountants, bookkeepers, analysts,
    # auditors and read-only executives.
    permission_classes = [IsAuthenticated, CanViewRegulatoryReturns]

    def list(self, request):
        qs = NBFIRAReturn.objects.all().select_related(
            'company', 'initiated_by', 'reviewed_by', 'approved_by', 'locked_by', 'submitted_by',
        )
        rt = request.query_params.get('type')
        if rt:
            qs = qs.filter(type=rt)
        comp = request.query_params.get('company')
        if comp:
            # id-only filtering raised a UUID ValidationError (500) whenever the
            # value was a code. Accept both. CFO standing rule 2026-07-28.
            from core.mixins import resolve_company
            co = resolve_company(comp)
            qs = qs.filter(company_id=co.id) if co else qs.none()
        out = [self._summary(r) for r in qs[:100]]
        return Response({'results': out, 'count': qs.count()})

    def retrieve(self, request, pk=None):
        r = get_object_or_404(NBFIRAReturn, pk=pk)
        return Response(self._detail(r))

    def create(self, request):
        rt   = (request.data.get('type') or 'quarterly').lower()
        plbl = (request.data.get('period_label') or '').strip().upper()
        comp = request.data.get('company') or None

        if rt not in ('quarterly', 'annual'):
            return Response({'detail': 'type must be quarterly or annual.'}, status=400)
        try:
            start, end = _parse_period(plbl) if rt == 'quarterly' else _parse_annual_period(plbl)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)

        company = None
        if comp:
            company = Company.objects.filter(pk=comp).first()

        with transaction.atomic():
            ret = NBFIRAReturn.objects.create(
                type=rt, period_label=plbl,
                period_start=start, period_end=end,
                company=company, initiated_by=request.user,
            )
            _audit(ret, request.user, 'create',
                   comment=f'Created {rt} draft {plbl}',
                   request=request)
            self._generate_lines(ret)

        return Response(self._detail(ret), status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def regenerate(self, request, pk=None):
        ret = get_object_or_404(NBFIRAReturn, pk=pk)
        if ret.status in (NBFIRAReturn.Status.LOCKED, NBFIRAReturn.Status.SUBMITTED):
            return Response({'detail': f'Cannot regenerate a {ret.status} return.'}, status=409)
        with transaction.atomic():
            ret.lines.all().delete()
            self._generate_lines(ret)
            _audit(ret, request.user, 'generate',
                   comment='Schedules regenerated from GL', request=request)
        return Response(self._detail(ret))

    # ── Phase 4 — workflow transitions ───────────────────────────────
    def _transition(self, request, pk, fn, action_label, require_comment=False):
        ret = get_object_or_404(NBFIRAReturn, pk=pk)
        comment = (request.data.get('comment') or '').strip()
        if require_comment and not comment:
            return Response({'detail': 'Comment is required.'}, status=400)
        before = {'status': ret.status}
        try:
            fn(ret, request.user, comment) if fn.__code__.co_argcount >= 3 else fn(ret, request.user)
        except (ValueError,) as exc:
            return Response({'detail': str(exc)}, status=409)
        _audit(ret, request.user, action_label, comment=comment,
               before=before, after={'status': ret.status}, request=request)
        return Response(self._detail(ret))

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        return self._transition(request, pk, wf.review, 'review')

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._transition(request, pk, wf.approve, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._transition(request, pk, wf.reject, 'reject', require_comment=True)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        return self._transition(request, pk, wf.reopen, 'reopen', require_comment=True)

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        return self._transition(request, pk, wf.lock, 'lock')

    @action(detail=True, methods=['post'], url_path='submit-to-nbfira')
    def submit_to_nbfira(self, request, pk=None):
        ret = get_object_or_404(NBFIRAReturn, pk=pk)
        comment   = (request.data.get('comment') or '').strip()
        filing    = (request.data.get('filing_reference') or '').strip()
        ack       = (request.data.get('acknowledgement_ref') or '').strip()
        before = {'status': ret.status}
        try:
            wf.submit(ret, request.user, comment,
                      filing_reference=filing, acknowledgement_ref=ack)
        except (ValueError,) as exc:
            return Response({'detail': str(exc)}, status=409)
        _audit(ret, request.user, 'submit', comment=comment,
               before=before, after={'status': ret.status,
                                     'filing_reference': filing,
                                     'acknowledgement_ref': ack},
               request=request)
        return Response(self._detail(ret))

    # ── Phase 4 — line edit (draft + reopened only) ─────────────────
    @action(detail=True, methods=['patch'], url_path='lines/(?P<line_id>[^/.]+)')
    def edit_line(self, request, pk=None, line_id=None):
        ret = get_object_or_404(NBFIRAReturn, pk=pk)
        if ret.status in (NBFIRAReturn.Status.LOCKED, NBFIRAReturn.Status.SUBMITTED):
            return Response({'detail': f'Cannot edit lines on a {ret.status} return.'}, status=409)
        line = get_object_or_404(NBFIRAReturnLine, pk=line_id, return_obj=ret)
        before = {'value': str(line.value)}
        try:
            new = Decimal(str(request.data.get('value', '0')))
        except Exception:    # noqa: BLE001
            return Response({'detail': 'value must be numeric.'}, status=400)
        line.value = new
        line.save(update_fields=['value', 'updated_at'])
        _audit(ret, request.user, 'edit',
               comment=f'Set {line.schedule}:{line.line_code} = {new}',
               before=before, after={'value': str(new)}, request=request)
        return Response({'id': str(line.id), 'value': str(new)})

    # ── Phase 4 — XLSX export ────────────────────────────────────────
    @action(detail=True, methods=['get'], url_path='export-xlsx')
    def export_xlsx(self, request, pk=None):
        from django.http import HttpResponse
        ret = get_object_or_404(NBFIRAReturn, pk=pk)
        content = build_workbook(ret)
        fhash = file_hash(content)
        _audit(ret, request.user, 'export',
               comment=f'XLSX export ({fhash[:12]}…)',
               after={'sha256': fhash}, request=request)
        resp = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename_for(ret)}"'
        return resp

    # ── Phase 4 — audit log read ─────────────────────────────────────
    # -- Filed return documents (CFO 2026-08-17) --------------------------
    # Omni GENERATES its schedules from the GL; these endpoints hold the return
    # that was actually FILED with NBFIRA, attached to the same period, so the
    # two can be compared. Uploading is allowed at ANY status including locked
    # and submitted -- the filed document is evidence about the period, not a
    # figure in the return, so attaching it must never be blocked by the
    # workflow state (you usually only HAVE the filed copy after submission).
    @action(detail=True, methods=['post'], url_path='upload-filed',
            parser_classes=[MultiPartParser, FormParser])
    def upload_filed(self, request, pk=None):
        r = get_object_or_404(NBFIRAReturn, pk=pk)

        f = request.FILES.get('file')
        if f is None:
            return Response({'detail': 'Choose a file to upload.'}, status=400)

        suffix = os.path.splitext(f.name or '')[1].lower()
        if suffix not in FILED_DOC_EXTENSIONS:
            allowed = ', '.join(sorted(FILED_DOC_EXTENSIONS))
            return Response(
                {'detail': f'That file type is not accepted. Upload one of: {allowed}.'},
                status=400)
        if not f.size:
            return Response({'detail': 'That file is empty.'}, status=400)
        if f.size > FILED_DOC_MAX_BYTES:
            mb = FILED_DOC_MAX_BYTES // (1024 * 1024)
            return Response(
                {'detail': f'That file is {f.size / 1048576:.1f} MB. The limit is {mb} MB.'},
                status=400)

        statement = (request.data.get('statement') or '').strip().upper()
        if statement not in FILED_DOC_STATEMENTS:
            ok = ', '.join(sorted(x for x in FILED_DOC_STATEMENTS if x))
            return Response(
                {'detail': f'Statement must be one of {ok}, or left blank for a '
                           f'full workbook.'},
                status=400)

        # Hash the upload for integrity + duplicate detection. Read in chunks so
        # a 25 MB workbook never sits in memory twice.
        digest = hashlib.sha256()
        for chunk in f.chunks():
            digest.update(chunk)
        file_sha = digest.hexdigest()
        f.seek(0)

        dupe = (NBFIRAFiledDocument.objects
                .filter(return_obj=r, file_hash_sha256=file_sha).first())
        if dupe is not None:
            return Response(
                {'detail': f'That exact file is already attached to this period '
                           f'as "{dupe.original_name}".',
                 'existing_id': str(dupe.id)},
                status=409)

        try:
            with transaction.atomic():
                doc = NBFIRAFiledDocument.objects.create(
                    return_obj=r,
                    file=f,
                    original_name=(f.name or 'return')[:255],
                    statement=statement,
                    size_bytes=f.size or 0,
                    content_type=(getattr(f, 'content_type', '') or '')[:100],
                    file_hash_sha256=file_sha,
                    notes=(request.data.get('notes') or '').strip(),
                    uploaded_by=request.user,
                )
                _audit(r, request.user, NBFIRAAuditLog.Action.UPLOAD,
                       comment=(f'Attached filed return "{doc.original_name}"'
                                + (f' (statement {statement})' if statement else '')),
                       after={'document_id': str(doc.id),
                              'original_name': doc.original_name,
                              'statement': statement,
                              'size_bytes': doc.size_bytes},
                       request=request)
        except Exception:                       # noqa: BLE001
            # Storage backends fail in their own ways (permissions, disk, path
            # length). Never let that surface as a blank 500 -- the commissions
            # upload shipped exactly that bug.
            log.exception('nbfira: could not store filed return %r on %s',
                          getattr(f, 'name', '?'), r.period_label)
            return Response(
                {'detail': 'The file could not be saved. Try again, and if it '
                           'keeps failing tell IT -- the details are in the log.'},
                status=500)

        return Response(self._filed_doc(doc),
                        status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='filed')
    def filed(self, request, pk=None):
        r = get_object_or_404(NBFIRAReturn, pk=pk)
        docs = r.filed_documents.select_related('uploaded_by')
        return Response({'results': [self._filed_doc(d) for d in docs]})

    @action(detail=True, methods=['get'],
            url_path='filed/(?P<doc_id>[^/.]+)/download')
    def download_filed(self, request, pk=None, doc_id=None):
        r = get_object_or_404(NBFIRAReturn, pk=pk)
        doc = get_object_or_404(NBFIRAFiledDocument, pk=doc_id, return_obj=r)
        if not doc.file:
            raise Http404('The stored file is missing.')
        try:
            handle = doc.file.open('rb')
        except (FileNotFoundError, OSError):
            # The row can outlive the blob (restored DB, moved media root).
            # Say so plainly instead of 500-ing.
            log.warning('nbfira: filed document %s has no file on disk (%s)',
                        doc.pk, doc.file.name)
            raise Http404('The stored file is missing from disk.')
        return FileResponse(handle, as_attachment=True,
                            filename=doc.original_name or 'filed-return')

    @action(detail=True, methods=['get', 'put'], url_path='a1-inputs')
    def a1_inputs_view(self, request, pk=None):
        """The three A.1 figures the ledger cannot supply.

        Same audience gate as the rest of this viewset (management + FC + FM),
        so whoever may read a filed return may maintain the assumptions behind
        it. GET also offers a `suggested` block read straight out of an attached
        filed workbook — a starting point to check and save, never applied on
        its own. A regulatory assumption gets saved by a person, under their
        name, with a stated source.
        """
        r = get_object_or_404(NBFIRAReturn, pk=pk)
        row = NBFIRAA1Input.objects.filter(return_obj=r).first()

        if request.method == 'GET':
            suggested = None
            doc = r.filed_documents.order_by('-uploaded_at').first()
            if doc and doc.file:
                try:
                    handle = doc.file.open('rb')
                except (FileNotFoundError, OSError):
                    log.warning('nbfira: a1-inputs could not open filed doc %s',
                                doc.pk)
                else:
                    try:
                        got = parse_a1_inputs(handle)
                    except Exception:            # noqa: BLE001
                        log.exception('nbfira: a1-inputs parse failed on %s', doc.pk)
                        got = {'ok': False}
                    finally:
                        handle.close()
                    if got.get('ok'):
                        suggested = {
                            'from_document': doc.original_name,
                            'anwp':        got['anwp'],
                            'mer_total':   got['mer_total'],
                            'net_assets':  got['net_assets'],
                            'alloc_mrctr': got['alloc_mrctr'],
                            'note':        got.get('note', ''),
                        }
            return Response({'saved': self._a1_input(row), 'suggested': suggested})

        # ── PUT ──────────────────────────────────────────────────────────
        if r.status in (NBFIRAReturn.Status.LOCKED, NBFIRAReturn.Status.SUBMITTED):
            return Response(
                {'detail': f'This return is {r.status}. Reopen it before '
                           f'changing the assumptions behind it.'}, status=409)

        note = (request.data.get('source_note') or '').strip()
        if not note:
            # These drive a regulatory figure. An assumption with no stated
            # source cannot be evidenced later, so it is not accepted.
            return Response(
                {'detail': 'Say where these figures came from before saving — '
                           'e.g. "as per the filed Q4 workbook".'}, status=400)

        def clean_map(raw, allowed):
            out = {}
            for k, v in (raw or {}).items():
                if k not in allowed:
                    continue
                try:
                    out[k] = str(Decimal(str(v)))
                except (InvalidOperation, ValueError, TypeError):
                    raise ValueError(f'"{v}" is not a number (for {k}).')
            return out

        buckets = {key for key, _l, _f in DEFAULT_MRC}
        try:
            anwp   = clean_map(request.data.get('anwp'), set(INSURANCE_CLASSES))
            assets = clean_map(request.data.get('net_assets'), buckets)
            alloc  = clean_map(request.data.get('alloc_mrctr'), buckets)
            mer    = Decimal(str(request.data.get('mer_total') or 0))
        except (ValueError, InvalidOperation) as exc:
            return Response({'detail': str(exc)}, status=400)

        # Coerce the date BEFORE it reaches the model. update_or_create sets the
        # attribute to whatever it is handed, so a string 'YYYY-MM-DD' stays a
        # string on the in-memory row -- and _a1_input() then calls .isoformat()
        # on it and raises AttributeError. The write had already COMMITTED by
        # then (the atomic block closes above the return), so every save made on
        # 19 Aug 2026 landed correctly while the screen showed
        # her a 500 and she reasonably concluded nothing had saved.
        eff = request.data.get('effective_from') or None
        if eff is not None and not isinstance(eff, date):
            eff_parsed = parse_date(str(eff).strip())
            if eff_parsed is None:
                return Response(
                    {'detail': f'"{eff}" is not a date. Use YYYY-MM-DD.'}, status=400)
            eff = eff_parsed

        with transaction.atomic():
            row, _created = NBFIRAA1Input.objects.update_or_create(
                return_obj=r,
                defaults={
                    'anwp': anwp, 'mer_total': mer,
                    'net_assets': assets, 'alloc_mrctr': alloc,
                    'source_note': note, 'effective_from': eff,
                    'entered_by': request.user,
                },
            )
            _audit(r, request.user, NBFIRAAuditLog.Action.EDIT,
                   comment=f'A.1 assumptions saved — {note}',
                   after={'anwp': anwp, 'mer_total': str(mer),
                          'net_assets': assets, 'alloc_mrctr': alloc},
                   request=request)
            # The figures only mean anything once the schedules are rebuilt on
            # them; leaving that to a separate click is how a stale target gets
            # read as the current one. Clear first — lines are unique per
            # (return, schedule, line_code), so rebuilding on top of the old set
            # collides instead of replacing.
            r.lines.all().delete()
            self._generate_lines(r)

        return Response({'saved': self._a1_input(row),
                         'detail': 'Saved, and A.1 has been recomputed.'})

    def _a1_input(self, row) -> Optional[dict]:
        if row is None:
            return None
        return {
            'anwp':           row.anwp,
            'mer_total':      str(row.mer_total),
            'net_assets':     row.net_assets,
            'alloc_mrctr':    row.alloc_mrctr,
            'source_note':    row.source_note,
            'effective_from': (row.effective_from.isoformat()
                               if row.effective_from else None),
            'entered_by':     (getattr(row.entered_by, 'username', '') or ''),
            'entered_at':     row.entered_at.isoformat() if row.entered_at else None,
        }

    @action(detail=True, methods=['get'],
            url_path='filed/(?P<doc_id>[^/.]+)/reconcile')
    def reconcile_filed(self, request, pk=None, doc_id=None):
        """Filed figure beside Omni's figure, line by line, for schedule A.1.

        Read-only on purpose: it parses the stored workbook on each call and
        writes nothing. The figures here are evidence for a human to compare —
        this endpoint must never become a route by which a filed document
        changes a return's own lines.
        """
        r = get_object_or_404(NBFIRAReturn, pk=pk)
        doc = get_object_or_404(NBFIRAFiledDocument, pk=doc_id, return_obj=r)
        if not doc.file:
            raise Http404('The stored file is missing.')

        try:
            handle = doc.file.open('rb')
        except (FileNotFoundError, OSError):
            log.warning('nbfira: filed document %s has no file on disk (%s)',
                        doc.pk, doc.file.name)
            raise Http404('The stored file is missing from disk.')
        try:
            parsed = parse_a1(handle)
        except Exception:                       # noqa: BLE001
            log.exception('nbfira: could not parse filed document %s', doc.pk)
            parsed = {'ok': False,
                      'error': 'The file could not be read. Tell IT — the '
                               'details are in the log.'}
        finally:
            handle.close()

        if not parsed.get('ok'):
            return Response({
                'document': self._filed_doc(doc),
                'ok': False,
                'error': parsed.get('error', 'Could not read this file.'),
                'rows': [],
            })

        filed_vals = parsed['values']
        omni_lines = {
            l.line_code: l for l in
            NBFIRAReturnLine.objects.filter(return_obj=r, schedule='A.1')
        }

        # Omni's own order first, then any line the workbook carried that Omni
        # never built — so nothing filed is quietly dropped off the comparison.
        codes = [l.line_code for l in
                 sorted(omni_lines.values(), key=lambda x: x.sort_order)]
        codes += [c for c in filed_vals if c not in omni_lines]

        rows = []
        for code in codes:
            line = omni_lines.get(code)
            filed = filed_vals.get(code)
            omni = line.value if line is not None else None
            diff = (filed - omni) if (filed is not None and omni is not None) else None
            rows.append({
                'line_code': code,
                'label':     line.label if line is not None else code,
                'filed':     (str(filed) if filed is not None else None),
                'omni':      (str(omni) if omni is not None else None),
                'difference': (str(diff) if diff is not None else None),
                'agrees':    (diff is not None and abs(diff) < Decimal('0.01')),
            })

        filed_pct = filed_vals.get('A1_PCT')
        omni_pct = getattr(omni_lines.get('A1_PCT'), 'value', None)

        return Response({
            'document': self._filed_doc(doc),
            'ok':      True,
            'schedule': parsed['sheet'],
            'as_at':   parsed.get('as_at', ''),
            'units':   "P'000",
            'note':    parsed.get('note', ''),
            'rows':    rows,
            'ratios':  {k: str(v) for k, v in (parsed.get('ratios') or {}).items()},
            'summary': {
                'filed_pct': (str(filed_pct) if filed_pct is not None else None),
                'omni_pct':  (str(omni_pct) if omni_pct is not None else None),
                'difference': (str(filed_pct - omni_pct)
                               if (filed_pct is not None and omni_pct is not None)
                               else None),
            },
            'method_warning': A1_METHOD_WARNING,
        })

    @action(detail=True, methods=['delete'],
            url_path='filed/(?P<doc_id>[^/.]+)')
    def delete_filed(self, request, pk=None, doc_id=None):
        r = get_object_or_404(NBFIRAReturn, pk=pk)
        doc = get_object_or_404(NBFIRAFiledDocument, pk=doc_id, return_obj=r)
        # Only the uploader or a superuser may detach -- a filed regulatory
        # document is not something any passing user should be able to remove.
        if not (request.user.is_superuser or doc.uploaded_by_id == request.user.pk):
            return Response(
                {'detail': 'Only the person who attached this file, or an '
                           'administrator, can remove it.'},
                status=403)
        name = doc.original_name
        _audit(r, request.user, NBFIRAAuditLog.Action.UNATTACH,
               comment=f'Removed filed return "{name}"',
               before={'document_id': str(doc.id), 'original_name': name},
               request=request)
        doc.delete()
        return Response(status=drf_status.HTTP_204_NO_CONTENT)

    def _filed_doc(self, d: NBFIRAFiledDocument) -> dict:
        return {
            'id':            str(d.id),
            'original_name': d.original_name,
            'statement':     d.statement,
            'size_bytes':    d.size_bytes,
            'content_type':  d.content_type,
            'file_hash_sha256': d.file_hash_sha256,
            'notes':         d.notes,
            'uploaded_by':   (getattr(d.uploaded_by, 'username', '') or ''),
            'uploaded_at':   d.uploaded_at.isoformat() if d.uploaded_at else None,
            'parsed':        d.parsed,
            'parse_note':    d.parse_note,
            'download_url':  (f'/api/v1/nbfira/returns/{d.return_obj_id}'
                              f'/filed/{d.id}/download/'),
        }

    @action(detail=True, methods=['get'], url_path='audit-log')
    def audit_log(self, request, pk=None):
        ret = get_object_or_404(NBFIRAReturn, pk=pk)
        rows = ret.audit_logs.select_related('user').order_by('-timestamp')[:200]
        return Response({'count': rows.count() if hasattr(rows, 'count') else len(list(rows)),
                         'results': [{
                             'id':        str(r.id),
                             'action':    r.action,
                             'user':      r.user.get_full_name() if r.user_id else '—',
                             'username':  r.user.username if r.user_id else '',
                             'comment':   r.comment,
                             'timestamp': r.timestamp.isoformat(),
                             'before':    r.before_json,
                             'after':     r.after_json,
                             'ip':        r.ip_address,
                         } for r in rows]})

    # [^/]+ not [^/.]+ — schedule codes contain a dot ('A.1'), so the old
    # pattern could never match the one schedule anyone asks about and this
    # endpoint 404'd for it.
    @action(detail=True, methods=['get'], url_path='schedule/(?P<code>[^/]+)')
    def schedule(self, request, pk=None, code=None):
        ret = get_object_or_404(NBFIRAReturn, pk=pk)
        lines = ret.lines.filter(schedule=code).order_by('sort_order')
        return Response({
            'return_id': str(ret.id),
            'schedule':  code,
            'lines':     [self._line(l) for l in lines],
        })

    # ── helpers ──────────────────────────────────────────────────────
    def _generate_lines(self, ret: NBFIRAReturn):
        gen = generate_annual if ret.type == 'annual' else generate_quarterly
        kwargs = {'company_id': str(ret.company_id) if ret.company_id else None}
        if gen is generate_quarterly:
            # So A.1 picks up the entered assumptions for THIS period.
            kwargs['return_obj'] = ret
        rows = gen(ret.period_start, ret.period_end, **kwargs)
        for r in rows:
            NBFIRAReturnLine.objects.create(
                return_obj=ret,
                schedule=r['schedule'], section=r.get('section', ''),
                line_code=r['line_code'], label=r['label'],
                value=Decimal(str(r['value'])), sort_order=r.get('sort_order', 0),
                source_accounts=r.get('source_accounts', []),
                formula=r.get('formula', ''),
            )

    def _summary(self, r: NBFIRAReturn) -> dict:
        return {
            'id':            str(r.id),
            'type':          r.type,
            'period_label':  r.period_label,
            'period_start':  r.period_start.isoformat() if r.period_start else None,
            'period_end':    r.period_end.isoformat() if r.period_end else None,
            'status':        r.status,
            'company':       r.company.code if r.company_id else None,
            'created_at':    r.created_at.isoformat() if r.created_at else None,
        }

    def _detail(self, r: NBFIRAReturn) -> dict:
        lines = r.lines.order_by('schedule', 'sort_order', 'line_code')
        by_schedule: dict[str, list] = {}
        for l in lines:
            by_schedule.setdefault(l.schedule, []).append(self._line(l))
        d = self._summary(r)
        d['lines_by_schedule'] = by_schedule
        d['schedules'] = sorted(by_schedule.keys())
        d['filed_documents'] = [
            self._filed_doc(x)
            for x in r.filed_documents.select_related('uploaded_by')]
        return d

    def _line(self, l: NBFIRAReturnLine) -> dict:
        return {
            'id':              str(l.id),
            'schedule':        l.schedule,
            'section':         l.section,
            'line_code':       l.line_code,
            'label':           l.label,
            'value':           str(l.value),
            'sort_order':      l.sort_order,
            'source_accounts': l.source_accounts,
            'formula':         l.formula,
        }


# ─────────────────────────────────────────────────────────────────────────
# Capital factors — list + upsert (so Settings page can edit live).
# ─────────────────────────────────────────────────────────────────────────
class NBFIRACapitalFactorViewSet(viewsets.ViewSet):

    # Same gate (Fable 5, 2026-08-17): these rows are the IRC / MRC / MCR /
    # g-factors the Prescribed Capital Target is computed from, and this
    # viewset WRITES them. An ungated upsert let any staff login change the
    # capital target — worse than reading a return.
    permission_classes = [IsAuthenticated, CanViewRegulatoryReturns]

    def list(self, request):
        qs = NBFIRACapitalFactor.objects.all().order_by('kind', 'key', '-effective_from')
        return Response({'results': [
            {
                'id':            str(f.id),
                'kind':          f.kind,
                'key':           f.key,
                'factor_value':  str(f.factor_value),
                'effective_from': f.effective_from.isoformat(),
                'effective_to':   f.effective_to.isoformat() if f.effective_to else None,
                'notes':         f.notes,
            }
            for f in qs[:300]
        ]})
