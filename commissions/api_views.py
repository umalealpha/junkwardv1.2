"""commissions/api_views.py — commission submission endpoints.

Two tiers (see access.py): a plain user creates + sees only their own
submissions; a manager (group owner / Finance / CFO) sees all and reviews.
"""
from __future__ import annotations

import logging
import re

from django.db import IntegrityError
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import service
from .access import (
    FINAL, STATUS_STAGE, IsCommissionsReviewer, agent_for_user, can_export,
    can_review_stage, is_payroll, is_reviewer, stage_of, user_stages,
)
from .models import CommissionGroup, CommissionSubmission
from .serializers import CommissionGroupSerializer, CommissionSubmissionSerializer
from core.payroll_deadline import assert_can_submit, SubmissionDeadlinePassed

log = logging.getLogger(__name__)


class CommissionGroupViewSet(viewsets.ReadOnlyModelViewSet):
    """The three commission flows + their config (read-only to any signed-in user)."""
    queryset = CommissionGroup.objects.all()
    serializer_class = CommissionGroupSerializer
    permission_classes = [IsAuthenticated]


class CommissionSubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = CommissionSubmissionSerializer
    permission_classes = [IsAuthenticated]

    # Reviewer-only actions (by handler method name). get_permissions() pattern
    # (reliable across router + direct dispatch); the specific stage is checked
    # inside review(), and export is further restricted to the final approver.
    _REVIEWER_ACTIONS = {'review', 'final_approve', 'bulk_review', 'ai_check', 'summary', 'payout_export', 'email_statements', 'agents'}

    def get_permissions(self):
        if getattr(self, 'action', None) in self._REVIEWER_ACTIONS:
            return [IsCommissionsReviewer()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = (CommissionSubmission.objects
              .select_related('agent', 'group')
              .prefetch_related('lines'))
        user = self.request.user
        # ?mine=1 forces own-scope even for a reviewer (the "My commission" tab),
        # so a reviewer never loads/edits another agent's submission by accident.
        mine = self.request.query_params.get('mine') in ('1', 'true', 'True')
        if (is_reviewer(user) or is_payroll(user)) and not mine:
            group = self.request.query_params.get('group')
            period = self.request.query_params.get('period')
            if group:
                qs = qs.filter(group__key=group)
            if period:
                qs = qs.filter(period_label=period)
            # CFO "view any agent" (2026-07-21): a reviewer may focus one agent —
            # returns that agent's submissions INCLUDING drafts (queue not set).
            agent_id = self.request.query_params.get('agent')
            if agent_id:
                qs = qs.filter(agent_id=agent_id)
            # ?queue=1 → only items awaiting THIS user's action (their review
            # stage, plus 'approved' for payroll = awaiting processing).
            if self.request.query_params.get('queue') in ('1', 'true', 'True'):
                stages = user_stages(user)
                statuses = [st for st, stg in STATUS_STAGE.items() if stg in stages]
                if is_payroll(user):
                    statuses.append(CommissionSubmission.Status.APPROVED)
                qs = qs.filter(status__in=statuses)
            return qs
        agent = agent_for_user(user)
        if not agent:
            return qs.none()
        return qs.filter(agent=agent)

    def _assert_editable(self, sub):
        S = CommissionSubmission.Status
        if sub.status not in (S.DRAFT, S.REJECTED):
            raise PermissionDenied('This submission is locked (already submitted or approved).')

    def perform_create(self, serializer):
        # Monthly submission deadline (CFO 2026-08-28): after the 16th only
        # the CFO may load commissions; staff are blocked.
        try:
            assert_can_submit(self.request.user)
        except SubmissionDeadlinePassed as exc:
            raise PermissionDenied(exc.message)
        # The client never sends the agent id (see serializer). Bind it here: a
        # plain user always gets their own agent; a manager may pass one, else self.
        user = self.request.user
        chosen = serializer.validated_data.get('agent')
        if is_reviewer(user):
            agent = chosen or agent_for_user(user)
        else:
            agent = agent_for_user(user)
            if chosen and agent and chosen.id != agent.id:
                raise PermissionDenied('You can only submit your own commission.')
        if not agent:
            raise PermissionDenied('No agent profile is linked to your account.')
        try:
            serializer.save(agent=agent)
        except IntegrityError:
            # DB unique (agent, period_label) — the month already has a submission.
            raise ValidationError({'period_label': 'A submission for this agent and month already exists.'})

    def perform_update(self, serializer):
        self._assert_editable(serializer.instance)
        # agent is immutable after create — never let a PATCH reassign the payee.
        serializer.validated_data.pop('agent', None)
        # Audit trail (CFO 2026-07-17): snapshot the lines BEFORE the save, then
        # after; if a staff amendment actually changed them, record one
        # CommissionAmendment (who / old→new / gross) with an Aria note. The
        # audit write is best-effort — it never blocks the edit.
        from .amend_audit import record_amendment, snapshot_lines
        before = snapshot_lines(serializer.instance)
        before_gross = serializer.instance.gross_commission
        serializer.save()
        sub = serializer.instance
        after = snapshot_lines(sub)
        if after != before:
            try:
                record_amendment(sub, self.request.user, before, after,
                                  before_gross, sub.gross_commission)
            except Exception:
                import logging
                logging.getLogger('commissions').exception(
                    'amend-audit write failed for submission %s', sub.id)

    def perform_destroy(self, instance):
        # Only an unfinished submission may be deleted. An approved/paid record is
        # part of the maker-checker trail and must not be erasable — least of all
        # by its own payee (that would free the one-per-month slot for a re-submit).
        S = CommissionSubmission.Status
        if instance.status not in (S.DRAFT, S.REJECTED):
            raise PermissionDenied('Only a draft or rejected submission can be deleted.')
        instance.delete()

    @action(detail=False, methods=['get'], url_path='access')
    def access(self, request):
        """Who am I, may I review, and which agent/group am I? Drives the UI."""
        agent = agent_for_user(request.user)
        stages = sorted(user_stages(request.user))
        return Response({
            'email': (request.user.email or '').lower(),
            'name': request.user.get_full_name(),
            'is_reviewer': bool(stages),
            'stages': stages,                     # e.g. ['stage1'] / ['stage2'] / ['final']
            'can_export': can_export(request.user),
            'is_payroll': is_payroll(request.user),
            'agent': agent.name if agent else None,
            'agent_id': str(agent.id) if agent else None,
            'group': agent.group.key if agent else None,
            'group_name': agent.group.name if agent else None,
            'pays_via': agent.group.pays_via if agent else None,
            'works_via_company': agent.works_via_company if agent else None,
            # The rate the FE should show live — 0 for a via-company / payroll agent.
            'withholding_rate': (str(service.effective_withholding_rate(agent, agent.group))
                                 if agent else None),
        })

    @action(detail=False, methods=['get'], url_path='agents')
    def agents(self, request):
        """Reviewer-only agent directory for the CFO 'view any agent' picker
        (id + name + code). Gated by _REVIEWER_ACTIONS → IsCommissionsReviewer."""
        from .models import CommissionAgent
        rows = (CommissionAgent.objects.filter(is_active=True)
                .order_by('name').values('id', 'name', 'agent_code'))
        return Response([{'id': str(r['id']), 'name': r['name'], 'code': r['agent_code']}
                         for r in rows])

    @action(detail=False, methods=['post'], url_path='push-to-payroll',
            permission_classes=[IsCommissionsReviewer])
    def push_to_payroll(self, request):
        """Finance: push a month's approved payroll-group commissions into the
        pending payroll batch. Auto-runs on approval; this recovers stragglers
        and is idempotent (safe to press repeatedly). Payroll authority only."""
        if not (is_payroll(request.user) or can_export(request.user)):
            raise PermissionDenied('Only Finance/CFO can push commissions to payroll.')
        from .payroll_feed import feed_period
        period = (request.data.get('period') or '').strip()
        if not re.match(r'^\d{4}-(0[1-9]|1[0-2])$', period):
            return Response({'detail': 'Pick a month (YYYY-MM).'}, status=400)
        return Response(feed_period(period_label=period, user=request.user))

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        sub = self.get_object()  # queryset already scopes a non-manager to their own
        # Re-submitting a REJECTED sub is always allowed — the rejection is
        # what forced the fix (Bokani Makosha 2026-09-17). A first-time DRAFT
        # submit still respects the monthly deadline.
        if sub.status != CommissionSubmission.Status.REJECTED:
            try:
                assert_can_submit(request.user)
            except SubmissionDeadlinePassed as exc:
                return Response({'detail': exc.message, 'code': 'deadline_passed'}, status=403)
        try:
            service.submit(sub, request.user)
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        return Response(self.get_serializer(sub).data)

    @action(detail=True, methods=['post'], url_path='review')
    def review(self, request, pk=None):
        sub = self.get_object()
        stage = stage_of(sub)
        if not stage:
            return Response({'detail': f'This submission is {sub.get_status_display()} — not awaiting review.'},
                            status=409)
        if not can_review_stage(request.user, stage):
            return Response({'detail': 'You are not a reviewer for this stage.'}, status=403)
        # Parse robustly: form-encoded 'false'/'0' must NOT read as True
        # (bool('false') is True) — this is the approve/reject decision.
        approve = str(request.data.get('approve')).strip().lower() in ('true', '1', 'yes')
        note = request.data.get('note') or ''
        try:
            service.review(sub, request.user, approve, note)
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        return Response(self.get_serializer(sub).data)

    @action(detail=True, methods=['post'], url_path='final-approve')
    def final_approve(self, request, pk=None):
        """CFO direct approve / send-back from any stage (CFO 2026-08-18).
        Gated to the FINAL approver; the specific check is inside the service."""
        sub = self.get_object()
        if not can_review_stage(request.user, FINAL):
            return Response({'detail': 'Only the final approver can approve directly.'}, status=403)
        approve = str(request.data.get('approve')).strip().lower() in ('true', '1', 'yes')
        note = request.data.get('note') or ''
        try:
            service.final_approve(sub, request.user, approve, note)
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        return Response(self.get_serializer(sub).data)

    @action(detail=False, methods=['post'], url_path='bulk-review')
    def bulk_review(self, request):
        """Approve several submissions in one click (CFO 2026-08-22 — "approve the
        clean ones"). Each id still goes through the SAME service.review() — stage
        check + full separation of duties — so this only BATCHES what the reviewer
        could already approve one by one. It never rejects (a reject needs a
        reason) and never bypasses a stage. Returns which ids went through and
        which were skipped (with why)."""
        ids = request.data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return Response({'detail': 'No commissions selected.'}, status=400)
        # Explicit cap, NOT a silent truncation — a real review queue is never
        # this big, and quietly dropping ids would read as "approved" when it
        # wasn't (panel 2026-08-22).
        if len(ids) > 200:
            return Response({'detail': 'Too many selected at once — pick 200 or fewer.'}, status=400)
        ids = [str(i) for i in ids]
        # Only well-formed UUIDs reach the DB filter — a malformed id would make
        # filter(id__in=...) raise and 500 the whole batch; here it just skips
        # as 'not found' (Fable note 2026-08-22).
        import uuid as _uuid
        def _valid(i):
            try:
                _uuid.UUID(i); return True
            except (ValueError, TypeError, AttributeError):
                return False
        valid_ids = [i for i in ids if _valid(i)]
        subs = {str(s.id): s for s in self.get_queryset().filter(id__in=valid_ids)}
        approved, skipped = [], []
        for sid in ids:
            sub = subs.get(sid)
            if sub is None:
                skipped.append({'id': sid, 'reason': 'not found'})
                continue
            try:
                service.review(sub, request.user, True, '')
                approved.append(sid)
            except ValueError as e:
                skipped.append({'id': sid, 'reason': str(e)})
        return Response({'ok': True, 'approved': approved, 'skipped': skipped})

    @action(detail=True, methods=['post'], url_path='ai-check')
    def ai_check(self, request, pk=None):
        """Aria's on-demand second opinion on ONE commission (CFO 2026-08-22).
        Read-only: it returns a plain-English note and changes nothing. Grounded
        in the deterministic review_flags; best-effort (falls back to the flags if
        the AI is unavailable)."""
        sub = self.get_object()
        from .ai_review import ai_opinion
        return Response(ai_opinion(sub))

    @action(detail=False, methods=['post'], url_path='upload')
    def upload(self, request):
        """Upload a commission workbook → parse its per-policy table → create/
        replace that agent's submission for the month (draft). Seamless bulk load,
        no retyping. Any signed-in user may upload their OWN sheet; a reviewer may
        upload for any agent (agent name from the `agent` field or the filename).
        The file is parsed server-side and deleted — client data never leaves the box."""
        # Deadline gate (CFO 2026-08-28): after the 16th only the CFO may
        # upload; staff see the red 'ask CFO' message. EXCEPTION (Bokani
        # Makosha 2026-09-17): a REJECTED submission must reopen for the
        # SAME agent + month — a re-upload is the fix for a rejection and
        # is only possible while a rejection stands; it never creates a
        # second slot (unique(agent, period)) and never overwrites an
        # approved sub (importer refuses).
        _period_probe = (request.data.get('period') or '').strip()
        _agent_probe = agent_for_user(request.user)
        _has_rejected = bool(
            _agent_probe and _period_probe
            and CommissionSubmission.objects.filter(
                agent=_agent_probe, period_label=_period_probe,
                status=CommissionSubmission.Status.REJECTED).exists())
        if not _has_rejected:
            try:
                assert_can_submit(request.user)
            except SubmissionDeadlinePassed as exc:
                return Response({'detail': exc.message, 'code': 'deadline_passed'}, status=403)
        import os
        import tempfile
        from . import importer
        f = request.FILES.get('file')
        period = (request.data.get('period') or '').strip()
        if not f:
            return Response({'detail': 'No file uploaded.'}, status=400)
        if getattr(f, 'size', 0) > 60 * 1024 * 1024:
            return Response({'detail': 'File too large (max 60 MB).'}, status=400)
        if not re.match(r'^\d{4}-(0[1-9]|1[0-2])$', period):
            return Response({'detail': 'Pick a month (YYYY-MM).'}, status=400)
        agent_name = (request.data.get('agent') or '').strip() or None
        group_key = (request.data.get('group') or '').strip() or 'independent'
        _flag = lambda k: str(request.data.get(k)).strip().lower() in ('true', '1', 'yes')
        # In-house sheets are a per-AGENT gross summary (one file = many people);
        # everything else is a per-POLICY workbook (one file = one agent).
        inhouse = _flag('inhouse')
        # A reviewer bulk-loading verified sheets can push them straight into the
        # review chain (submit=1), so agents don't each have to log in and submit.
        submit = _flag('submit')
        # preview=1 parses + returns what we read WITHOUT writing — the loader's
        # "check before you commit" step. Nothing is created; the file is deleted.
        preview = _flag('preview')
        # own=1 → the "My commission" tab: this is the signed-in user's OWN sheet.
        # Bind it to their own agent, per-policy, no auto-submit — REGARDLESS of
        # whether they are also a reviewer. (The is_reviewer branch below only
        # forces own-binding for non-reviewers, so a reviewer uploading their own
        # sheet used to fall through to the reviewer path and mis-bind it —
        # Bokani Makosha, bug f3a02295.)
        own = _flag('own')
        if own or not is_reviewer(request.user):
            ag = agent_for_user(request.user)
            if not ag:
                raise PermissionDenied('No agent profile is linked to your account.')
            agent_name, group_key = ag.name, ag.group.key   # force own
            inhouse, submit = False, False   # own upload = per-policy draft, never auto-submit
        suffix = os.path.splitext(f.name)[1].lower()
        # Accept every spreadsheet flavour the house reader handles: .xlsx/.xlsm
        # via openpyxl, .xlsb via pyxlsb, and .xls/.ods via python-calamine (the
        # reader the rest of the codebase standardises on). An old-format .xls
        # used to 500 (2026-07-18) then was rejected outright; it now parses like
        # the rest, so no one has to re-save an old workbook to upload it.
        if suffix not in ('.xlsx', '.xlsm', '.xlsb', '.xls', '.ods'):
            return Response({'detail': 'Upload a spreadsheet (.xlsx, .xlsm, .xlsb, .xls or .ods).'},
                            status=400)
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            for chunk in f.chunks():
                tmp.write(chunk)
            tmp.close()
            # Auto-detect: an in-house upload that actually carries a per-policy
            # table (Policy Number + Commissions) is loaded as DETAIL, not as a
            # per-agent gross summary — so the reviewer keeps the basis of each
            # commission ("i can't see what the commission is based on", Bokani
            # Makosha, bug f3a02295). A genuine agent-summary sheet has no
            # per-policy header, so it still takes the summary path. When we
            # switch, the group stays in_house (its rate/pays_via are the agent's).
            if inhouse:
                try:
                    if importer.has_per_policy_table(tmp.name):
                        inhouse = False
                        # we only get here when this WAS an in-house upload, so
                        # the detailed single-agent sheet belongs to in_house —
                        # pin it (the default 'independent' is truthy, so a bare
                        # `or` never corrected a missing group). Fable note, 08-22.
                        group_key = 'in_house'
                except Exception:
                    log.exception('commission upload: per-policy detection failed for %r', f.name)
            if preview:
                r = self._preview_workbook(importer, tmp.name, period, inhouse, agent_name)
            elif inhouse:
                r = importer.import_inhouse(tmp.name, period, commit=True, submit=submit)
            else:
                r = importer.import_workbook(tmp.name, group_key, period,
                                            agent_name=agent_name, commit=True, submit=submit)
        except ValueError as e:
            # Aria adds a plain-English "why + fix" when she can (best-effort;
            # never picks amounts — the import itself stays fully deterministic).
            hint = self._ai_upload_hint(tmp.name, inhouse)
            msg = (f"Couldn't read that workbook — {e}. "
                   "Use the standard template, or enter the lines by hand.")
            return Response({'detail': (msg + ' ' + hint) if hint else msg}, status=400)
        except Exception:
            # A corrupt/odd file must never surface as a blank 500 ("server
            # error", Bokani 2026-07-18) — tell the uploader something
            # actionable and keep the traceback in the log.
            log.exception('commission upload failed to parse %r (period %s)', f.name, period)
            hint = self._ai_upload_hint(tmp.name, inhouse)
            msg = ("Couldn't read that file as an Excel workbook. "
                   "Re-save it as .xlsx and try again, or enter the lines by hand.")
            return Response({'detail': (msg + ' ' + hint) if hint else msg}, status=400)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        # Keep the uploaded workbook on the submission so a reviewer can download
        # and verify it before approving (CFO 2026-08-12). Only per-policy uploads
        # map to a single submission; in-house bulk sheets create many and are not
        # attached. A failure to store the file never fails the upload itself.
        sub_id = r.get('submission_id') if not preview else None
        if sub_id:
            try:
                sub = CommissionSubmission.objects.get(id=sub_id)
                f.seek(0)
                sub.statement_file.save(f.name, f, save=False)
                sub.statement_filename = f.name[:255]
                sub.save(update_fields=['statement_file', 'statement_filename'])
            except Exception:  # noqa: BLE001
                log.exception('commission upload: could not store statement file for %s', sub_id)
        return Response({'ok': True, **r})

    @staticmethod
    def _ai_upload_hint(path, inhouse) -> str:
        """Aria's plain-English 'why it didn't load + the fix' for a failed
        upload. Best-effort, never raises, never chooses import amounts."""
        try:
            from .ai_upload_assist import explain_upload_problem
            return explain_upload_problem(path, inhouse=bool(inhouse))
        except Exception:
            log.info('commission upload: AI hint helper failed', exc_info=True)
            return ''

    @staticmethod
    def _preview_workbook(importer, path, period, inhouse, agent_name):
        """Dry-run parse for the loader's preview step. Returns the rows we read
        (never writes). Rows are capped so a huge sheet can't bloat the response."""
        from decimal import Decimal
        CAP = 500
        if inhouse:
            pairs = importer.parse_inhouse_summary(path, period)
            total = sum((g for _, g in pairs), Decimal('0'))
            return {'mode': 'inhouse', 'count': len(pairs), 'total': str(total),
                    'rows': [{'name': n, 'gross': str(g)} for n, g in pairs[:CAP]],
                    'truncated': len(pairs) > CAP}
        lines = importer.parse_workbook(path)
        total = sum((l['commission_amount'] for l in lines), Decimal('0'))
        return {'mode': 'policy', 'agent': agent_name or importer.derive_agent_name(path),
                'count': len(lines), 'total': str(total),
                'rows': [{'policy_number': l.get('policy_number', ''),
                          'client_name': l.get('client_name', ''),
                          'transaction_type': l.get('transaction_type', ''),
                          'amount_collected': str(l.get('amount_collected') or ''),
                          'commission_amount': str(l.get('commission_amount') or '')}
                         for l in lines[:CAP]],
                'truncated': len(lines) > CAP}

    @action(detail=True, methods=['post'], url_path='mark-processed')
    def mark_processed(self, request, pk=None):
        """Payroll marks an approved submission processed → paid → notifies
        Bokani/Tlamelo (cc Pako/Kago)."""
        if not is_payroll(request.user):
            return Response({'detail': 'Only payroll can mark a commission processed.'}, status=403)
        sub = self.get_object()
        try:
            service.mark_processed(sub, request.user)
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        return Response(self.get_serializer(sub).data)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        group_key = request.query_params.get('group')
        period = request.query_params.get('period')
        group = CommissionGroup.objects.filter(key=group_key).first() if group_key else None
        return Response(service.monthly_summary(group=group, period_label=period))

    @action(detail=False, methods=['get'], url_path='payout-export')
    def payout_export(self, request):
        if not can_export(request.user):
            return Response({'detail': 'Only the final approver can export the payout file.'}, status=403)
        group_key = request.query_params.get('group')
        period = request.query_params.get('period')
        group = CommissionGroup.objects.filter(key=group_key).first()
        if not group or not period:
            return Response({'detail': 'group and period are required.'}, status=400)
        csv_text, meta = service.export_payout_csv(group, period, user=request.user)
        resp = HttpResponse(csv_text, content_type='text/csv')
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', f'{group.key}_{period}')[:60]
        resp['Content-Disposition'] = f'attachment; filename="commission_payout_{safe}.csv"'
        resp['X-Payout-Held'] = str(meta['held_count'])
        return resp

    @action(detail=False, methods=['post'], url_path='email-statements')
    def email_statements(self, request):
        """Month-close: email each approved agent in a group+month their own
        statement (CFO 2026-08-22). Restricted to the payout exporter (CFO/
        Finance), like payout_export. preview=1 is a dry-run: returns who WOULD
        be emailed, sends nothing."""
        if not can_export(request.user):
            return Response({'detail': 'Only the final approver can email statements.'}, status=403)
        group_key = (request.data.get('group') or '').strip()
        period = (request.data.get('period') or '').strip()
        group = CommissionGroup.objects.filter(key=group_key).first()
        if not group or not period:
            return Response({'detail': 'group and period are required.'}, status=400)
        preview = str(request.data.get('preview')).strip().lower() in ('true', '1', 'yes')
        r = service.email_agent_statements(group, period, commit=not preview)
        return Response({'ok': True, 'preview': preview, **r})

    @action(detail=True, methods=['get'], url_path='statement')
    def statement(self, request, pk=None):
        """Download the original uploaded workbook for this submission, so a
        reviewer can verify it before approving (Bokani Makosha 2026-08-12)."""
        sub = self.get_object()
        if not is_reviewer(request.user):
            return Response({'detail': 'Only a commission reviewer may download the statement.'},
                            status=403)
        if sub.statement_file:
            from django.http import FileResponse
            name = re.sub(r'[^A-Za-z0-9._-]+', '_',
                          sub.statement_filename or f'statement_{sub.period_label}.xlsx')[:80]
            return FileResponse(sub.statement_file.open('rb'), as_attachment=True, filename=name)
        # No raw workbook kept (uploads before 2026-08-13 were parsed then discarded)
        # — regenerate a statement CSV from the stored lines, so EVERY submission is
        # verifiable, not only new uploads (Bokani Makosha: her current queue).
        lines = sub.lines.all().order_by('policy_number')
        if not lines:
            return Response({'detail': 'No statement or line detail is stored for this submission.'},
                            status=404)
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Policy number', 'Client', 'Type', 'Frequency', 'Amount collected',
                    'Annualised premium', 'Commission rate', 'Amount applicable',
                    'Collection date', 'Commission'])
        for ln in lines:
            w.writerow([ln.policy_number, ln.client_name, ln.transaction_type, ln.frequency,
                        ln.amount_collected, ln.annualised_premium, ln.commission_rate,
                        ln.amount_applicable, ln.collection_date or '', ln.commission_amount])
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', f'{sub.agent.name}_{sub.period_label}')[:60]
        resp = HttpResponse(buf.getvalue(), content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="commission_statement_{safe}.csv"'
        return resp
