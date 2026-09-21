"""agent_portal/api_views.py — Agent Portal (sales commissions) endpoints."""
from __future__ import annotations

import logging

from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import service
from .access import IsAgentPortalManager, agent_for_user, is_manager
from .commission_engine import STREAMS
from .models import Agent, AgentBankAccount, CommissionCycle, CommissionLine
log = logging.getLogger('agent_portal.bank_access')


from .serializers import (
    AgentBankAccountSerializer, AgentSerializer, CommissionCycleSerializer,
    CommissionLineSerializer,
)
from .bank_unlock import (
    is_bank_unlocked, mask_account, password_ok, unlock_hours, reveal_configured,
    can_reveal, unlock as bank_unlock_user, lock as bank_lock_user,
)


class AgentViewSet(viewsets.ModelViewSet):
    queryset = Agent.objects.all()
    serializer_class = AgentSerializer
    # Managers only — agents + their bank details are the money surface. A
    # non-manager reaches their own figures via the cycles `my-profile` action.
    permission_classes = [IsAgentPortalManager]

    @action(detail=True, methods=['get', 'put'], url_path='bank')
    def bank(self, request, pk=None):
        """GET / upsert the agent's bank details (sensitive — auth-gated)."""
        agent = self.get_object()
        # DPA audit: agent bank details are personal financial data — log every
        # read/write with the acting user. (Hard role-restriction to Finance is a
        # follow-up once the operator role is confirmed — see PR notes.)
        log.info('bank_%s user=%s agent=%s', request.method, request.user.id, agent.id)
        if request.method == 'GET':
            b = getattr(agent, 'bank', None)
            data = AgentBankAccountSerializer(b).data if b else {}
            # Mask the account number unless this manager has entered the reveal
            # password (Motlatsi 2026-07-22). Masked view still lets them see the
            # rest; to view/verify the number they unlock first.
            if data:
                revealed = is_bank_unlocked(request.user)
                if not revealed:
                    data = {**data, 'account_number': mask_account(data.get('account_number', ''))}
                data['revealed'] = revealed
            return Response(data)
        # Fable review #2: validate FIRST, then write — get_or_create before
        # validation left an empty bank row behind (which made the agent look
        # "ready to pay"). An account number is required to save.
        ser = AgentBankAccountSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        acct = (ser.validated_data.get('account_number') or '').strip()
        existing = getattr(agent, 'bank', None)
        if not acct and not (existing and (existing.account_number or '').strip()):
            return Response({'detail': 'An account number is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        b, _ = AgentBankAccount.objects.update_or_create(
            agent=agent, defaults={**ser.validated_data, 'updated_by': request.user})
        return Response(AgentBankAccountSerializer(b).data)

    @action(detail=False, methods=['get'], url_path='bank-accounts')
    def bank_accounts(self, request):
        """All captured bank accounts + submission coverage (how many agents have
        submitted). Sensitive (DPA) — audit-logged. Hard role-restriction is a
        follow-up (CFO to provide controls)."""
        revealed = is_bank_unlocked(request.user)
        # Audit the acting user AND whether full numbers were served (DPA).
        log.info('bank_LIST user=%s revealed=%s', request.user.id, revealed)
        total = Agent.objects.count()
        rows = []
        for b in AgentBankAccount.objects.select_related('agent'):
            if not (b.account_number or '').strip():
                continue
            acct = b.account_number if revealed else mask_account(b.account_number)
            rows.append({
                'agent_id': str(b.agent_id), 'agent_name': b.agent.name,
                'bank_name': b.bank_name, 'account_number': acct,
                'branch_code': b.branch_code, 'account_name': b.account_name,
                'updated_at': b.updated_at,
            })
        rows.sort(key=lambda r: r['agent_name'])
        return Response({'submitted': len(rows), 'total_agents': total, 'accounts': rows,
                         'revealed': revealed, 'unlock_hours': unlock_hours(),
                         'reveal_configured': reveal_configured(),
                         'can_reveal': can_reveal(request.user)})

    @action(detail=False, methods=['post'], url_path='bank-unlock')
    def bank_unlock(self, request):
        """Second-factor: enter the reveal password to see full account numbers
        for a short window. Manager-gated by the viewset; further restricted to
        the CFO/CEO/COO; fail-closed if no server password is configured."""
        if not can_reveal(request.user):
            log.info('bank_UNLOCK_DENIED user=%s (not CFO/CEO/COO)', request.user.id)
            return Response({'detail': 'Only the CFO, CEO and COO may reveal account numbers.',
                             'revealed': False}, status=status.HTTP_403_FORBIDDEN)
        if not reveal_configured():
            return Response(
                {'detail': 'Number reveal is not enabled yet — ask the CFO to set the reveal password.',
                 'revealed': False, 'reveal_configured': False},
                status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not password_ok((request.data or {}).get('password')):
            log.info('bank_UNLOCK_FAIL user=%s', request.user.id)
            return Response({'detail': 'Incorrect reveal password.', 'revealed': False},
                            status=status.HTTP_401_UNAUTHORIZED)
        until = bank_unlock_user(request.user)
        log.info('bank_UNLOCK user=%s until=%s', request.user.id, until.isoformat())
        return Response({'revealed': True, 'unlocked_until': until.isoformat(),
                         'unlock_hours': unlock_hours()})

    @action(detail=False, methods=['post'], url_path='bank-lock')
    def bank_lock(self, request):
        """Re-mask now (manual 'Hide numbers')."""
        bank_lock_user(request.user)
        return Response({'revealed': False})

    @action(detail=False, methods=['get'], url_path='bank-lock-status')
    def bank_lock_status(self, request):
        return Response({'revealed': is_bank_unlocked(request.user),
                         'unlock_hours': unlock_hours(),
                         'reveal_configured': reveal_configured()})


class CommissionCycleViewSet(viewsets.ModelViewSet):
    queryset = CommissionCycle.objects.all()
    serializer_class = CommissionCycleSerializer
    # Manager-only by default (CFO 2026-07-07). The two exceptions below —
    # `access` (who am I?) and `my-profile` (my own figures) — are open to any
    # signed-in user; get_permissions() relaxes just those.
    permission_classes = [IsAgentPortalManager]

    # Actions any authenticated user may call (everyone else = profile-only).
    _OPEN_ACTIONS = {'access', 'my_profile'}

    def get_permissions(self):
        if getattr(self, 'action', None) in self._OPEN_ACTIONS:
            return [IsAuthenticated()]
        return [IsAgentPortalManager()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'], url_path='access')
    def access(self, request):
        """Who is the caller, and may they manage the portal? Drives the UI:
        managers see every tab; everyone else sees only their profile."""
        return Response({
            'email': (request.user.email or '').lower(),
            'name': request.user.get_full_name(),
            'is_manager': is_manager(request.user),
        })

    @action(detail=False, methods=['get'], url_path='my-profile')
    def my_profile(self, request):
        """A non-manager's own Agent Profile only: their commission lines in the
        latest cycle + totals + KYC/payment/approval breakdown. Empty (never
        another person's data) when the signed-in user isn't a matched agent."""
        agent = agent_for_user(request.user)
        cycle = CommissionCycle.objects.order_by('-created_at').first()
        if not agent or not cycle:
            return Response({'matched': False, 'agent': None,
                             'cycle': (cycle.label if cycle else None),
                             'lines': [], 'summary': {}})
        lines = list(CommissionLine.objects.filter(cycle=cycle, agent=agent)
                     .order_by('stream'))
        payable = [l for l in lines if l.payable]
        return Response({
            'matched': True,
            'agent': agent.name,
            'cycle': cycle.label,
            'summary': {
                'total_lines': len(lines),
                'approved': len(payable),
                'rejected': len(lines) - len(payable),
                'payable_bwp': str(sum((l.commission for l in payable),
                                       __import__('decimal').Decimal('0.00'))),
            },
            'lines': CommissionLineSerializer(lines, many=True).data,
        })

    @action(detail=False, methods=['get'], url_path='streams')
    def streams(self, request):
        """The stream catalogue (key, name, tag) for the UI."""
        return Response([{'key': k, 'name': v[0], 'tag': v[1], 'uses_cutoff': v[3]}
                         for k, v in STREAMS.items()])

    @action(detail=True, methods=['post'], url_path='ingest')
    def ingest(self, request, pk=None):
        """POST {stream, text} — compute + store a stream's lines. If
        stream == 'all_policies', split the extract into New Sales + Motor."""
        cycle = self.get_object()
        if cycle.status != CommissionCycle.Status.OPEN:
            return Response({'detail': f'Cycle is {cycle.get_status_display()}; reopen it before ingesting.'},
                            status=status.HTTP_409_CONFLICT)
        text = request.data.get('text') or ''
        stream = request.data.get('stream') or ''
        if not text.strip():
            return Response({'detail': 'No CSV text provided.'}, status=status.HTTP_400_BAD_REQUEST)
        from datetime import date as _date
        rd = None
        rds = (request.data.get('report_date') or '').strip()
        if rds:
            try:
                rd = _date.fromisoformat(rds)
            except ValueError:
                rd = None
        extras = {
            'submitted_by': request.user,
            'agent_name': (request.data.get('agent_name') or '').strip(),
            'report_date': rd,
        }
        src = request.data.get('source') or 'paste'
        if src not in ('paste', 'sheet', 'hand'):
            src = 'paste'
        try:
            if stream == 'all_policies':
                out = service.ingest_all_policies_csv(cycle, text, **extras)
            else:
                out = service.ingest_stream_csv(cycle, stream, text, source=src, **extras)
        except (ValueError, IndexError) as e:
            # IndexError guard (Fable review #1) — engine pads rows now, but a
            # malformed paste must return a readable 400, never a 500.
            return Response({'detail': f'Could not parse the pasted data: {e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(out)

    @action(detail=True, methods=['get'], url_path='payrun')
    def payrun(self, request, pk=None):
        return Response(service.payrun_summary(self.get_object()))

    @action(detail=True, methods=['get', 'post'], url_path='source-reports')
    def source_reports(self, request, pk=None):
        """Reports tab: POST {kind, text} to store a control report; GET lists them."""
        from .models import SourceReport
        from .serializers import SourceReportSerializer
        cycle = self.get_object()
        if request.method == 'POST':
            text = request.data.get('text') or ''
            kind = request.data.get('kind') or ''
            if not text.strip():
                return Response({'detail': 'No report rows provided.'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                rep = service.add_source_report(cycle, kind, text, user=request.user)
            except ValueError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response(SourceReportSerializer(rep).data, status=status.HTTP_201_CREATED)
        qs = SourceReport.objects.filter(cycle=cycle).select_related('uploaded_by')[:100]
        return Response(SourceReportSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='policy-status-check')
    def policy_status_check(self, request, pk=None):
        """Cross-check payable lines against the latest All Policies Report."""
        return Response(service.policy_status_check(self.get_object()))

    @action(detail=True, methods=['get'], url_path='submissions')
    def submissions(self, request, pk=None):
        """The upload log for this cycle — who submitted what, when (latest first)."""
        from .models import ReportSubmission
        from .serializers import ReportSubmissionSerializer
        qs = ReportSubmission.objects.filter(cycle=self.get_object()).select_related('submitted_by')[:300]
        return Response(ReportSubmissionSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='lines')
    def lines(self, request, pk=None):
        qs = CommissionLine.objects.filter(cycle=self.get_object()).select_related('agent')
        stream = request.query_params.get('stream')
        if stream:
            qs = qs.filter(stream=stream)
        payable = request.query_params.get('payable')
        if payable is not None:
            qs = qs.filter(payable=payable.lower() == 'true')
        agent = request.query_params.get('agent')
        if agent:
            qs = qs.filter(agent_id=agent)
        # No silent truncation (a June cycle exceeds 1000 lines and clipped
        # rows understate money on screen). ?limit= caps explicitly; the
        # 20k ceiling is a DoS guard, far above any real cycle.
        try:
            limit = min(int(request.query_params.get('limit', 20000)), 20000)
        except ValueError:
            limit = 20000
        return Response(CommissionLineSerializer(qs[:limit], many=True).data)

    @action(detail=True, methods=['get'], url_path='payout')
    def payout(self, request, pk=None):
        ready, held = service.payout_rows(self.get_object())
        return Response({'ready': ready, 'held': held,
                         'ready_count': len(ready), 'held_count': len(held)})

    @action(detail=True, methods=['get'], url_path='payout-export')
    def payout_export(self, request, pk=None):
        import re as _re
        cycle = self.get_object()
        csv_text, batch = service.export_payout_csv(cycle, user=request.user)
        resp = HttpResponse(csv_text, content_type='text/csv')
        # Sanitize the label for the header (Fable review #17).
        safe = _re.sub(r'[^A-Za-z0-9._-]+', '_', cycle.label)[:60] or 'cycle'
        resp['Content-Disposition'] = f'attachment; filename="payout_{safe}.csv"'
        return resp

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """Sign off the cycle: lock it against re-ingest + stamp the approver."""
        try:
            c = service.approve_cycle(self.get_object(), request.user)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CommissionCycleSerializer(c).data)

    @action(detail=True, methods=['post'], url_path='reopen')
    def reopen(self, request, pk=None):
        """Unlock an approved cycle for corrections (a PAID cycle is refused)."""
        try:
            c = service.reopen_cycle(self.get_object())
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CommissionCycleSerializer(c).data)

    @action(detail=True, methods=['get', 'post'], url_path='payslips')
    def payslips(self, request, pk=None):
        """GET: list this cycle's agent payslips. POST: (re)build them from the
        cycle's payable commission + Bharath's tax method."""
        cycle = self.get_object()
        if request.method == 'POST':
            from .payslip_service import build_payslips_for_cycle
            try:
                summary = build_payslips_for_cycle(cycle, user=request.user)
            except ValueError as e:
                return Response({'detail': str(e)}, status=status.HTTP_409_CONFLICT)
            return Response(summary)
        return self._payslip_list(cycle)

    def _payslip_list(self, cycle):
        from .models import AgentPayslip
        from .serializers import AgentPayslipSerializer
        qs = (AgentPayslip.objects.filter(cycle=cycle)
              .select_related('agent').order_by('agent__name'))
        return Response(AgentPayslipSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='agent-review-xlsx')
    def agent_review_xlsx(self, request, pk=None):
        """One-click Excel of every agent's commission for the cycle (by stream,
        gross/tax/net, email/bank presence, not-paid count) for finance review.
        Carries bank PRESENCE only, never the account number."""
        from .review_xlsx import agent_review_workbook
        cycle = self.get_object()
        data = agent_review_workbook(cycle)
        fname = f"UniCoin_{cycle.label}_Agent_Review.xlsx".replace(' ', '_').replace('—', '-')
        resp = HttpResponse(
            data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{fname}"'
        return resp

    @action(detail=True, methods=['post'], url_path='verify-graphite')
    def verify_graphite(self, request, pk=None):
        """Bridge plan (CFO 2026-07-07): confirm each payable line against the
        live Graphite system — paid / active / KYC — instead of trusting the
        upload. Stamps every line and returns the tally. Never fails the request
        if the replica is down (lines come back 'unavailable')."""
        from .graphite_check import verify_cycle
        run = verify_cycle(self.get_object(), user=request.user)
        return Response({
            'lines_checked':  run.lines_checked,
            'ok':             run.ok_count,
            'mismatch':       run.mismatch_count,
            'not_found':      run.not_found_count,
            'skipped':        run.skipped_count,
            'unavailable':    run.unavailable,
            'checked_at':     run.created_at,
        })


class AgentPayslipViewSet(viewsets.ReadOnlyModelViewSet):
    """UniCoin Instant Insurance commission payslips — manager-only. Agents have
    no login; a manager downloads or emails each slip to the agent."""
    from .models import AgentPayslip as _AP
    from .serializers import AgentPayslipSerializer as _APS
    queryset = _AP.objects.select_related('agent', 'cycle').all()
    serializer_class = _APS
    permission_classes = [IsAgentPortalManager]

    def get_queryset(self):
        qs = super().get_queryset()
        cycle = self.request.query_params.get('cycle')
        if cycle:
            qs = qs.filter(cycle_id=cycle)
        return qs.order_by('agent__name')

    @action(detail=True, methods=['get'], url_path='pdf')
    def pdf(self, request, pk=None):
        """Download the agent's commission payslip PDF."""
        from .payslip_pdf import generate_agent_payslip_pdf
        ps = self.get_object()
        pdf = generate_agent_payslip_pdf(ps)
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{ps.number}.pdf"'
        return resp

    @action(detail=True, methods=['post'], url_path='email')
    def email(self, request, pk=None):
        """Email the payslip PDF to the agent's registered address. No login
        needed on the agent's side. Manager-triggered only."""
        from django.conf import settings as _s
        from django.core.mail import EmailMessage
        from django.utils import timezone
        from .payslip_pdf import generate_agent_payslip_pdf
        ps = self.get_object()
        # Do not email a slip before the cycle is signed off — emailing is an
        # irreversible send to the agent (Fable review 2026-07-27). Download is
        # allowed anytime for review; only the SEND waits for approval.
        if ps.cycle.status not in (CommissionCycle.Status.APPROVED, CommissionCycle.Status.PAID):
            return Response({'detail': 'Approve the pay cycle before emailing payslips to agents.'},
                            status=status.HTTP_409_CONFLICT)
        to = (ps.agent.email or '').strip()
        if not to or '@' not in to:
            return Response({'detail': 'This agent has no email address on file.'},
                            status=status.HTTP_400_BAD_REQUEST)
        pdf = generate_agent_payslip_pdf(ps)
        sender = getattr(_s, 'DEFAULT_FROM_EMAIL', '') or 'omni@alphadirect.co.bw'
        period = ''
        if ps.cycle.start_date and ps.cycle.end_date:
            period = f'{ps.cycle.start_date:%d %b} – {ps.cycle.end_date:%d %b %Y}'
        body = (f'{ps.agent.name}\n\n'
                f'Your UniCoin Instant Insurance commission payslip for '
                f'{ps.cycle.label} ({period}) is attached.\n\n'
                f'  Gross commission : BWP {ps.gross:,.2f}\n'
                f'  Income tax       : BWP {ps.tax:,.2f}\n'
                f'  Net payable      : BWP {ps.net:,.2f}\n\n'
                f'Payslip no. {ps.number}.\n'
                f'Queries: commissions@alphadirect.co.bw\n\n'
                f'UniCoin')
        msg = EmailMessage(subject=f'UniCoin commission payslip — {ps.cycle.label}',
                           body=body, from_email=sender, to=[to])
        msg.attach(f'{ps.number}.pdf', pdf, 'application/pdf')
        sent = msg.send(fail_silently=False)
        ps.emailed_at = timezone.now()
        ps.emailed_to = to
        ps.save(update_fields=['emailed_at', 'emailed_to', 'updated_at'])
        return Response({'sent': int(sent), 'to': to, 'emailed_at': ps.emailed_at})
