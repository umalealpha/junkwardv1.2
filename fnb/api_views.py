"""fnb/api_views.py — DRF endpoints for the FNB integration."""

import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.http import Http404
from core.permissions import CanExportEftBatch, CanViewFinancials
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from banking.models import BankAccount
from .client import FNBClient, FNBConfig, FNBNotConfigured, FNBAPIError
from .endpoints import PING
from .models import FNBBatchSubmission, FNBSyncLog, FNBWebhookEvent
from .payments import submit_eft_batch, refresh_batch_status
from .serializers import (
    FNBBatchSubmissionSerializer,
    FNBStatusSerializer,
    FNBSyncLogSerializer,
    FNBWebhookEventSerializer,
)
from .webhooks import dispatch_webhook, record_webhook


class CanManageFNB(BasePermission):
    """Finance-leadership / CFO gate (Fable H5). These audit feeds carry raw
    payment payloads — vendor account numbers, amounts, full bank statements —
    so they must NOT be readable by every authenticated staffer. Resolves
    _can_manage_fnb at request time (defined later in this module)."""
    def has_permission(self, request, view):
        return _can_manage_fnb(request.user)


# ---------------------------------------------------------------------------
# Read-only audit endpoints
# ---------------------------------------------------------------------------

class FNBSyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset           = FNBSyncLog.objects.select_related('triggered_by_user').order_by('-created_at')
    serializer_class   = FNBSyncLogSerializer
    permission_classes = [CanManageFNB]

    def get_queryset(self):
        qs = self.queryset
        params = self.request.query_params
        if params.get('direction'):
            qs = qs.filter(direction=params['direction'])
        if params.get('service'):
            qs = qs.filter(service=params['service'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        return qs


class FNBBatchSubmissionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset           = FNBBatchSubmission.objects.select_related(
                             'source_account', 'submitted_by',
                         ).order_by('-created_at')
    serializer_class   = FNBBatchSubmissionSerializer
    permission_classes = [CanManageFNB]


class FNBWebhookEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset           = FNBWebhookEvent.objects.order_by('-received_at')
    serializer_class   = FNBWebhookEventSerializer
    permission_classes = [CanManageFNB]


# ---------------------------------------------------------------------------
# Status / readiness — for the /banking/fnb page
# ---------------------------------------------------------------------------

class FNBStatusView(APIView):
    # Stays open to ordinary staff — the /banking/fnb badge reads it (readiness
    # gate 2026-08-25). The bank API host and auth mode are shown only to finance
    # administrators; everyone else gets the booleans and counts (Fable 5.1
    # audit 2026-09-02, LOW).
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cfg = FNBConfig.from_settings()
        admin = _can_manage_fnb(request.user)
        last = FNBSyncLog.objects.order_by('-created_at').first()
        pending = FNBBatchSubmission.objects.filter(
            status=FNBBatchSubmission.Status.PENDING,
        ).count()
        since = timezone.now() - timedelta(hours=24)
        failed_24h = FNBSyncLog.objects.filter(
            status=FNBSyncLog.Status.FAILED, created_at__gte=since,
        ).count()
        data = {
            'configured':       cfg.is_configured,
            'auth_mode':        cfg.auth_mode if admin else ('set' if cfg.auth_mode else ''),
            'api_base':         (cfg.api_base or '(not set)') if admin else ('(set)' if cfg.api_base else '(not set)'),
            'last_sync_at':     last.created_at if last else None,
            'last_sync_status': last.status if last else 'no_calls',
            'pending_batches':  pending,
            'failed_calls_24h': failed_24h,
        }
        return Response(FNBStatusSerializer(data).data)


# ---------------------------------------------------------------------------
# Aria — plain-English AI read of the bank connection (CFO directive 2026-08-04)
# Gated to Finance/CFO (CanManageFNB): the signals summarise payment plumbing.
# The AI only ever sees non-sensitive, masked signals (see fnb/ai_health.py).
# ---------------------------------------------------------------------------

class FNBHealthSummaryView(APIView):
    permission_classes = [CanManageFNB]

    def get(self, request):
        from .ai_health import health_summary
        force = request.query_params.get('refresh') in ('1', 'true', 'yes')
        return Response(health_summary(force=force))


class FNBHealthAskView(APIView):
    permission_classes = [CanManageFNB]

    def post(self, request):
        from .ai_health import answer_question
        question = (request.data or {}).get('question', '')
        if not question:
            return Response({'detail': 'question is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(answer_question(question))


# ---------------------------------------------------------------------------
# Credentials — CFO sets the FNB login in-app (no server access needed)
# ---------------------------------------------------------------------------

def _can_manage_fnb(user) -> bool:
    """CFO-authority gate (the CFO's SSO account is NOT a Django superuser).
    Allow superuser OR is_administrator OR title=CFO — same trust level as the
    API-key console; setting the live bank login is high-trust."""
    # ONE copy of the rule. This used to be a second, drifted copy that never
    # looked at UserProfile.is_active, so "Deactivate user" left an offboarded
    # finance administrator with every FNB power (Fable 5.1 audit 2026-09-02, H8).
    from core.permissions import is_finance_administrator
    return is_finance_administrator(user)


class FNBCredentialView(APIView):
    """GET = current status (client id + whether a secret is on file — NEVER the
    secret itself). POST = save the client id + secret (secret encrypted at rest).
    CFO / administrator only."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can_manage_fnb(request.user):
            return Response({'detail': 'You do not have access to the bank connection.'},
                            status=status.HTTP_403_FORBIDDEN)
        from .models import FnbCredential
        row = FnbCredential.load()
        cfg = FNBConfig.from_settings()
        return Response({
            'client_id':  (row.client_id if (row and row.client_id) else cfg.client_id) or '',
            'secret_set': bool((row and row.has_secret) or cfg.client_secret),
            'configured': cfg.is_configured,
            'api_base':   cfg.api_base or '(not set)',
        })

    def post(self, request):
        if not _can_manage_fnb(request.user):
            return Response({'detail': 'You do not have access to the bank connection.'},
                            status=status.HTTP_403_FORBIDDEN)
        from .models import FnbCredential
        client_id     = (request.data.get('client_id') or '').strip()
        client_secret = (request.data.get('client_secret') or '').strip()
        if not client_id or not client_secret:
            return Response({'detail': 'Both the Client ID and the Client secret are required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        row = FnbCredential.load() or FnbCredential()
        row.client_id = client_id
        row.set_secret(client_secret)
        if request.user and request.user.is_authenticated:
            row.updated_by = request.user
        row.save()
        # Drop any cached OAuth token so the new login is used on the next call.
        try:
            from .client import _TOKEN_CACHE
            _TOKEN_CACHE.clear()
        except Exception:  # noqa: BLE001
            pass
        return Response({'ok': True, 'configured': FNBConfig.from_settings().is_configured})


# ---------------------------------------------------------------------------
# Connection test — pings the configured FNB base URL
# ---------------------------------------------------------------------------

class FNBTestConnectionView(APIView):
    """POST /api/v1/fnb/test-connection/

    Verifies omni can talk to FNB by minting an OAuth2 client_credentials
    token against /oauth2/token/v2. We deliberately do NOT probe a
    business endpoint here — FNB exposes no /health and the cheapest
    business call (POST notifications) consumes a quota slot in the
    10 req/min/endpoint throttle, which we want to reserve for live
    traffic. A working token round-trip proves credentials, network,
    OAuth flow, and TLS chain in one shot.
    """
    # SECURITY (2026-08-25, Manus retest P1): was IsAuthenticated, so any
    # signed-in staffer could mint a live OAuth token against the bank and
    # burn one of the 10 req/min slots the docstring above is at pains to
    # reserve. Testing the bank login is a configuration action — same gate
    # as setting it (FNBCredentialView).
    permission_classes = [CanManageFNB]

    def post(self, request):
        import time
        from .client import FNBConfig, _get_oauth_token
        try:
            cfg = FNBConfig.from_settings()
        except FNBNotConfigured as e:
            self._log(FNBSyncLog.Status.FAILED, 0, str(e)[:300])
            return Response(
                {'success': False, 'reason': 'not_configured', 'detail': str(e)},
                status=status.HTTP_200_OK,
            )
        t0 = time.time()
        try:
            token = _get_oauth_token(cfg)
        except FNBNotConfigured as e:
            self._log(FNBSyncLog.Status.FAILED, 0, str(e)[:300])
            return Response(
                {'success': False, 'reason': 'not_configured', 'detail': str(e)},
                status=status.HTTP_200_OK,
            )
        except Exception as e:                      # noqa: BLE001
            self._log(FNBSyncLog.Status.FAILED, 0, str(e)[:300])
            return Response(
                {'success': False, 'reason': 'auth_failed',
                 'detail': str(e)[:400]},
                status=status.HTTP_200_OK,
            )
        elapsed_ms = int((time.time() - t0) * 1000)
        self._log(FNBSyncLog.Status.SUCCESS, 200,
                  f'OAuth token len={len(token)} in {elapsed_ms}ms')
        return Response({
            'success':     True,
            'http_status': 200,
            'elapsed_ms':  elapsed_ms,
            'detail':      ('OAuth2 client_credentials token OK — credentials, '
                            'network and TLS chain healthy.'),
        })

    @staticmethod
    def _log(status_val, http_status, msg):
        try:
            # Fable M6: this used method=/path= — the model has http_method/
            # endpoint + a required `direction`, so every call raised TypeError
            # swallowed by the bare except, and NO connection test was ever
            # audit-logged (incl. auth failures with live creds). Correct fields:
            FNBSyncLog.objects.create(
                direction      = FNBSyncLog.Direction.OUTBOUND,
                service        = FNBSyncLog.Service.TEST,
                http_method    = 'POST',
                endpoint       = '/oauth2/token/v2',
                http_status    = http_status,
                status         = status_val,
                request_summary= 'Connection test (OAuth-only probe)',
                error_message  = '' if status_val == FNBSyncLog.Status.SUCCESS else msg[:1000],
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Submit batch — wired to the existing maker-checker approved Payment queue
# ---------------------------------------------------------------------------

class FNBSubmitBatchView(APIView):
    """POST /api/v1/fnb/submit-batch/

    Request body:
      {
        "payment_ids":          ["uuid", "uuid", ...],
        "source_account_id":    "uuid",   // banking.BankAccount.id
        "service_level_code":   "SDVA",   // optional, defaults SDVA
        "requested_execution_date": "2026-05-19"  // optional, defaults today
      }

    Preconditions enforced BY THIS VIEW (not merely assumed of the caller):
      * approval_status is APPROVED or NOT_REQUIRED — a PENDING payment is
        refused, so maker-checker cannot be skipped by calling this directly
      * the batch total is within settings.FNB_BATCH_MAX_BWP
      * Every Payment has vendor_bank_account set
      * Single currency across the batch

    On success, returns the FNBBatchSubmission row including FNB's
    instructionId. On failure, the batch is persisted in FAILED/PENDING
    state for audit.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from payments.models import Payment

        # Fable C2: submitting a real EFT batch moves money — gate it to the
        # finance/CFO authority (same trust level as setting the bank login),
        # not any authenticated staffer.
        if not _can_manage_fnb(request.user):
            return Response({'detail': 'Only Finance leadership / the CFO can submit '
                             'payments to the bank.'}, status=status.HTTP_403_FORBIDDEN)

        body = request.data or {}
        payment_ids = body.get('payment_ids') or []
        source_account_id = body.get('source_account_id')
        service_level_code = (body.get('service_level_code') or 'SDVA').upper()
        requested_execution_date = body.get('requested_execution_date') or None

        if not payment_ids:
            return Response({'detail': 'payment_ids is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not source_account_id:
            return Response({'detail': 'source_account_id is required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        source_account = get_object_or_404(BankAccount, pk=source_account_id)

        # Only fully-approved, GL-posted outbound payments qualify.
        # (Fable audit 2026-07-07: status='approved' is not a Payment.Status
        # value — the old filter matched NOTHING, so batch submission was
        # permanently dead. 'approved' lives on approval_status.)
        # ALLOW-LIST the approval states, do not merely exclude REJECTED.
        # exclude(REJECTED) also admits PENDING — a payment sitting in the
        # maker-checker queue, not yet approved by anyone — so the bank could
        # be paid before the second signature existed. The docstring above has
        # always claimed maker-checker was enforced here; it was not.
        # Fail-safe: name the states that may go, so a state added later is
        # refused by default rather than silently permitted.
        SENDABLE_APPROVAL = (
            Payment.ApprovalStatus.APPROVED,
            # Legacy / pay-run path that confirms directly under the
            # OutboundPaymentPolicy dual-auth threshold (see the model).
            Payment.ApprovalStatus.NOT_REQUIRED,
        )
        payments_qs = Payment.objects.filter(pk__in=payment_ids).filter(
            status=Payment.Status.CONFIRMED,
            payment_type=Payment.PaymentType.SENT,
            bank_submitted_at__isnull=True,   # never re-send a paid one (Fable audit)
            approval_status__in=SENDABLE_APPROVAL,
        )
        skipped = len(payment_ids) - payments_qs.count()
        if not payments_qs.exists():
            return Response(
                {'detail': 'No confirmed outbound payments matched payment_ids. '
                           f'{skipped} were skipped (not confirmed/sent, still '
                           'awaiting approval, or already sent to the bank).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fable C2: don't debit one company's FNB account for another company's
        # payments. BankAccount reaches its company via gl_account.owner_company
        # (nullable for the bank-integration account); enforce only when both
        # sides are known, so a GL-less FNB account still works.
        src_company = getattr(getattr(source_account, 'gl_account', None),
                              'owner_company_id', None)
        if src_company is not None:
            foreign = payments_qs.exclude(company_id=src_company)
            if foreign.exists():
                return Response(
                    {'detail': 'Some payments belong to a different company than the '
                     'source FNB account — submit each company from its own account.'},
                    status=status.HTTP_400_BAD_REQUEST)

        try:
            batch = submit_eft_batch(
                payments_qs,
                source_account=source_account,
                user=request.user,
                service_level_code=service_level_code,
                requested_execution_date=requested_execution_date,
            )
        except FNBNotConfigured as e:
            return Response(
                {'success': False, 'reason': 'not_configured', 'detail': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except FNBAPIError as e:
            return Response(
                {'success': False, 'reason': 'fnb_error',
                 'http_status': e.status_code, 'detail': e.body[:300]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ValidationError as e:
            return Response(
                {'success': False, 'reason': 'validation',
                 'detail': str(e.messages) if hasattr(e, 'messages') else str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            'success': True,
            'skipped_non_approved': skipped,
            'batch': FNBBatchSubmissionSerializer(batch).data,
        })


class FNBBankViewPreview(APIView):
    """POST /api/v1/fnb/bank-view-preview/

    Body: {"payment_id": "uuid"} for a saved payment, optionally with any of the
    three fields to preview an unsaved edit; or, for the once-off form before
    the payment exists, the raw form values:
      {"payee_name", "reference", "bank_beneficiary_name",
       "bank_our_reference", "bank_narration"}

    Returns exactly the three strings the bank will be given. The screen must
    not compute this itself — two implementations of one rule drift, and the
    operator would then be shown something different from what is sent (H74).

    Gated like every other payment read (2026-07-17 audit: payments were once
    readable by any authenticated user) and clamped to the caller's entities: a
    beneficiary name is exactly the sort of thing entity scoping exists to keep
    inside its own company.
    """
    permission_classes = [IsAuthenticated, CanViewFinancials]

    # A stand-in payment number for the not-yet-created case. Anything that
    # resolves to it is blanked before returning, because FNB will never see it.
    DRAFT_NUMBER = 'PAY-OUT-NEW'

    def post(self, request):
        from core.mixins import scoped_company_ids
        from payments.models import Payment
        from .payments import bank_view_of

        body = request.data or {}
        payment_id = body.get('payment_id')

        if payment_id:
            ids = scoped_company_ids(request)
            if ids == []:
                raise Http404
            qs = Payment.objects.all() if ids is None else \
                Payment.objects.filter(company_id__in=ids)
            payment = get_object_or_404(qs, pk=payment_id)
            # Preview the operator's unsaved edits against the real row.
            for field in ('bank_beneficiary_name', 'bank_our_reference',
                          'bank_narration'):
                if field in body:
                    setattr(payment, field, body.get(field) or '')
            return Response(bank_view_of(payment))

        # No payment yet (the once-off form). The stand-in must mirror what
        # payments.api_views would actually CREATE, or the panel claims a value
        # the bank will never receive. That view defaults a blank reference to
        # "One-off - <payee>" — the very string the CFO objected to — so a
        # preview that hid it would recommit the fault it exists to prevent.
        payee = (body.get('payee_name') or '').strip()
        typed_ref = (body.get('reference') or '').strip()

        class _Draft:
            payment_number = FNBBankViewPreview.DRAFT_NUMBER
            description = ''
            reference = (typed_ref or (f'One-off - {payee}' if payee else ''))[:200]
            payee_name = payee
            contact = None
            vendor_bank_account = None
            bank_beneficiary_name = body.get('bank_beneficiary_name') or ''
            bank_our_reference = body.get('bank_our_reference') or ''
            bank_narration = body.get('bank_narration') or ''

        view = bank_view_of(_Draft())
        # Our reference is the payment number until the payment exists, and the
        # stand-in is not it. Show nothing rather than a fake. The Omni marker
        # (e.g. " (O)") is appended by bank_view_of, so strip it before deciding
        # whether the base is still the draft placeholder.
        import re as _re

        def _is_draft(value: str) -> bool:
            # our_reference carries the origin marker e.g. (O) or (UNI);
            # narration falls back to the same draft number without it.
            # Strip any marker before comparing so BOTH are blanked.
            return _re.sub(r'\s*\([A-Z]{1,4}\)', '', value).strip() == FNBBankViewPreview.DRAFT_NUMBER

        return Response({
            k: ('' if _is_draft(v) else v)
            for k, v in view.items()
        })


class FNBRefreshBatchView(APIView):
    """POST /api/v1/fnb/batches/<batch_id>/refresh/

    Hit FNB's retrieveReport endpoint for this batch and update its status.
    """
    # SECURITY (2026-08-25, Manus retest P1): was IsAuthenticated. The report
    # carries per-transaction statuses and the bank's reject reasons for real
    # outbound payments, and refresh_batch_status WRITES batch + payment state
    # (it clears bank_submitted_at on a reject). CanExportEftBatch is the right
    # trust level — outbound-payment makers plus finance leadership — not
    # CanManageFNB (CFO/admin only), which would stop the accountant who
    # actually chases a batch all day.
    permission_classes = [CanExportEftBatch]

    def post(self, request, batch_id):
        batch = get_object_or_404(FNBBatchSubmission, pk=batch_id)
        try:
            body = refresh_batch_status(batch, user=request.user)
        except FNBNotConfigured as e:
            return Response({'success': False, 'reason': 'not_configured',
                             'detail': str(e)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except FNBAPIError as e:
            return Response({'success': False, 'http_status': e.status_code,
                             'detail': e.body[:300]},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            'success': True,
            'batch': FNBBatchSubmissionSerializer(batch).data,
            'fnb_status': body.get('groupStatus'),
            'fnb_reasons': body.get('statusReasonInformation') or [],
        })


# ---------------------------------------------------------------------------
# Webhook receiver — public endpoint, signature-verified
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pull statements — UI-driven counterpart to the cron command
# ---------------------------------------------------------------------------

class FNBPullStatementsView(APIView):
    """POST /api/v1/fnb/pull-statements/

    Body:
      {
        "bank_account_id": "uuid",       // banking.BankAccount.id
        "from_date":       "YYYY-MM-DD", // optional, defaults to 31d ago
        "to_date":         "YYYY-MM-DD"  // optional, defaults to today
      }

    Returns the BankStatement summary + lines count.
    """
    # SECURITY (2026-08-25, Manus retest P1): was IsAuthenticated, so any
    # signed-in staffer could pull a full bank statement — every counterparty,
    # narrative and amount on the company's account. Same trust level as the
    # EFT export (CanExportEftBatch), which guards data of the same
    # sensitivity; deliberately NOT CanViewFinancials, which also admits
    # auditors and read-only executives.
    permission_classes = [CanExportEftBatch]

    def post(self, request):
        from datetime import date as _date
        from .statements import pull_statement
        from banking.serializers import BankStatementDetailSerializer

        body = request.data or {}
        ba_id     = body.get('bank_account_id')
        from_str  = body.get('from_date')
        to_str    = body.get('to_date')

        if not ba_id:
            return Response({'detail': 'bank_account_id is required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        bank_account = get_object_or_404(BankAccount, pk=ba_id)
        if not bank_account.account_number or bank_account.account_number == '0':
            return Response(
                {'success': False,
                 'detail': "BankAccount.account_number is not set "
                           "(currently '0'). Edit the bank account first."},
                status=status.HTTP_200_OK,
            )

        def _parse(s):
            if not s:
                return None
            try:
                return _date.fromisoformat(s)
            except (TypeError, ValueError):
                return None

        from_date = _parse(from_str)
        to_date   = _parse(to_str)

        # FNB Botswana Statement v1 only accepts ranges within ONE calendar
        # month. A 31-Mar → 30-Apr request returns ORDS 400 "Bad Request"
        # with no detail. Surface the rule client-side so the operator
        # knows what to fix.
        if from_date and to_date and (
            from_date.year != to_date.year or from_date.month != to_date.month
        ):
            return Response(
                {'success': False, 'reason': 'cross_month_range',
                 'detail': ('FNB Statement API accepts only same-calendar-month '
                            f'ranges. {from_date} → {to_date} spans two months. '
                            'Pick a single month (e.g. 2026-04-01 → 2026-04-30).')},
                status=status.HTTP_200_OK,
            )

        try:
            stmt = pull_statement(
                bank_account,
                from_date=from_date,
                to_date=to_date,
                user=request.user,
            )
        except FNBNotConfigured as e:
            return Response(
                {'success': False, 'reason': 'not_configured', 'detail': str(e)},
                status=status.HTTP_200_OK,
            )
        except FNBAPIError as e:
            # Surface the real upstream message instead of masking as 502.
            # The frontend then renders the actual FNB cause in the toast
            # (e.g. "account not enabled on this environment") rather than
            # the generic "Server error HTTP 502" banner.
            body_preview = (e.body or '')[:400]
            return Response(
                {'success': False, 'reason': 'fnb_error',
                 'http_status': e.status_code,
                 'detail': (f'FNB rejected the request '
                            f'(HTTP {e.status_code}): {body_preview}. '
                            f'For UAT only account 63001966639 (ADIC FNB Sandbox) '
                            f'is enabled — real accounts need to be whitelisted '
                            f'by FNB.')},
                status=status.HTTP_200_OK,
            )
        except Exception as exc:                    # noqa: BLE001
            return Response(
                {'success': False, 'detail': str(exc)[:300]},
                status=status.HTTP_200_OK,
            )

        return Response({
            'success':         True,
            'statement_id':    str(stmt.id),
            'statement_number': stmt.statement_number,
            'statement_date':  stmt.statement_date,
            # Null, not "None": the bank may have sent no balance block.
            'opening_balance': (None if stmt.opening_balance is None
                                else str(stmt.opening_balance)),
            'closing_balance': (None if stmt.closing_balance is None
                                else str(stmt.closing_balance)),
            'line_count':      stmt.line_count,
            'file_name':       stmt.file_name,
        })


# ---------------------------------------------------------------------------
# Quick transfer — one-shot single-payment EFT for the FM
# ---------------------------------------------------------------------------

class FNBQuickTransferView(APIView):
    """POST /api/v1/fnb/quick-transfer/

    A single account-to-account transfer in one call:
      - Creates a synthetic payments.Payment (status=approved by CFO/FM)
      - Bundles it as a 1-transaction FNB batch
      - Submits via fnb.payments.submit_eft_batch
      - Returns the FNBBatchSubmission summary

    Body:
      {
        "source_account_id":    "uuid",   // banking.BankAccount (debtor)
        "beneficiary_account":  "63002130754",
        "beneficiary_name":     "Vendor Name",
        "beneficiary_bic":      "FIRNBWGX",    // optional, defaults FNB BW
        "amount":               1000.00,
        "currency":             "BWP",
        "reference":            "TEST 2026-05-25",
        "service_level":        "SDVA"         // optional
      }

    Authorisation: caller must be authenticated. Maker-checker is
    bypassed for the quick-transfer flow because the caller is acting
    as both maker AND checker by submitting through the UI directly.
    Audit trail is preserved via FNBSyncLog + the synthetic Payment row.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from decimal import Decimal as _Dec
        from django.conf import settings as _settings
        from payments.models import Payment
        from .payments import submit_eft_batch

        # BUG 55e44cbc (Oprah, controls audit) — this one-shot transfer bypasses
        # maker-checker (docstring admits it) and takes a free-text beneficiary
        # not tied to the Vendor Bank Accounts register, so any authenticated
        # user could move real money with zero second approval. Disabled by
        # default until the CFO signs off a proper control model (route through
        # the Vendor Bank register + the Payment Approvals second-approver, with
        # a CFO threshold). The controlled path already exists: create a Payment,
        # get it approved (maker != checker), then FNB batch-submit approved
        # payments. Flip FNB_QUICK_TRANSFER_ENABLED=True only after that sign-off.
        if not getattr(_settings, 'FNB_QUICK_TRANSFER_ENABLED', False):
            return Response(
                {'detail': 'Quick transfer is disabled pending an approval-control '
                           'redesign (CFO directive). Raise the payment in the Payments '
                           'queue so a second approver signs it off, then submit the '
                           'approved payment to FNB.'},
                status=status.HTTP_403_FORBIDDEN)
        # Fable C2/M5: even when the flag is on, restrict this maker-checker-
        # bypassing money mover to Finance leadership / the CFO — not any
        # authenticated user. The flag alone must never open it to everyone.
        if not _can_manage_fnb(request.user):
            return Response({'detail': 'Only Finance leadership / the CFO can make a '
                             'quick transfer.'}, status=status.HTTP_403_FORBIDDEN)

        b = request.data or {}
        src_id  = b.get('source_account_id')
        ben_acc = (b.get('beneficiary_account') or '').strip()
        ben_nm  = (b.get('beneficiary_name') or '').strip()
        ben_bic = (b.get('beneficiary_bic') or 'FIRNBWGX').strip()
        amt_raw = b.get('amount')
        ccy     = (b.get('currency') or 'BWP').upper().strip()
        ref     = (b.get('reference') or 'Quick transfer').strip()
        svc     = (b.get('service_level') or 'SDVA').upper().strip()

        # Validate -----------------------------------------------------------
        missing = [k for k, v in (
            ('source_account_id',   src_id),
            ('beneficiary_account', ben_acc),
            ('beneficiary_name',    ben_nm),
            ('amount',              amt_raw),
        ) if not v]
        if missing:
            return Response({'detail': f'Missing fields: {", ".join(missing)}'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            amount = _Dec(str(amt_raw)).quantize(_Dec('0.01'))
        except Exception:                                # noqa: BLE001
            return Response({'detail': 'amount must be numeric.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({'detail': 'amount must be > 0.'},
                            status=status.HTTP_400_BAD_REQUEST)

        source_account = get_object_or_404(BankAccount, pk=src_id)
        if not source_account.account_number or source_account.account_number == '0':
            return Response(
                {'detail': "Source BankAccount.account_number is not set."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build a transient stand-in Payment (no DB row) and hand it to
        # submit_eft_batch, which is now TWO-PHASE and manages its own
        # transactions around the FNB POST. Fable C1: do NOT wrap this in an
        # outer atomic() — that was what rolled back the audit record after the
        # money had already left the bank (CFO/Kago 2026-07-16 incident).
        # pk/id = None so no DB-row stamping treats the stand-in as real.
        try:
            class _Stand:
                pk = None
                id = None

            vba = _Stand()
            vba.account_holder_name = ben_nm
            vba.bank_name          = ben_nm
            vba.account_number     = ben_acc
            vba.branch_code        = ''
            vba.swift_bic          = ben_bic
            vba.account_type       = 'CACC'
            vba.email              = None

            p = _Stand()
            # FNB endToEndId max 35 chars
            p.payment_number       = f'QT-{uuid.uuid4().hex[:16].upper()}'
            p.amount               = amount
            p.amount_bwp           = amount if ccy == 'BWP' else amount
            p.currency_code_id     = ccy
            p.description          = ref
            p.reference            = ref
            p.vendor_bank_account  = vba
            p.remittance_email     = None
            # Readable bank-facing reference (CFO 2026-08-22). Without this, the
            # FNB "Our reference" falls back to the QT-<hex> payment number —
            # meaningless to whoever authorises the transfer at the bank. Same
            # treatment as the payments module: bank_view_of folds these to FNB's
            # limits and still appends the (O) Omni-origin marker. Uses the
            # operator's reference, or a "<from account> to <to>" default.
            transfer_label = (ref if ref and ref.lower() != 'quick transfer'
                              else f'{source_account.account_name} to {ben_nm}')
            p.bank_our_reference   = transfer_label
            p.bank_narration       = transfer_label

            batch = submit_eft_batch(
                [p],
                source_account=source_account,
                user=request.user,
                service_level_code=svc,
                # Quick transfer builds a transient stand-in with no author, so
                # there is no creator to compare a releaser against. Stated
                # explicitly rather than skipped by accident: this path is
                # behind FNB_QUICK_TRANSFER_ENABLED and is single-person by
                # design.
                allow_single_person=True,
            )
        except FNBNotConfigured as e:
            return Response(
                {'success': False, 'reason': 'not_configured', 'detail': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except FNBAPIError as e:
            return Response(
                {'success': False, 'reason': 'fnb_error',
                 'http_status': e.status_code, 'detail': e.body[:300]},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ValidationError as e:
            return Response(
                {'success': False, 'reason': 'validation',
                 'detail': str(e.messages) if hasattr(e, 'messages') else str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:                          # noqa: BLE001
            return Response(
                {'success': False, 'detail': str(exc)[:300]},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'success':       True,
            'batch':         FNBBatchSubmissionSerializer(batch).data,
            'reference':     ref,
            'amount':        str(amount),
            'currency':      ccy,
            'beneficiary':   {
                'name':           ben_nm,
                'account_number': ben_acc,
                'bic':            ben_bic,
            },
        })


# ---------------------------------------------------------------------------
# BankAccount account-number patch — surgical, needed for FNB mapping
# ---------------------------------------------------------------------------

class BankAccountSetNumberView(APIView):
    """PATCH /api/v1/banking/bank-accounts/<id>/set-account-number/

    All BankAccount rows on prod were imported with account_number = '0'.
    Real FNB account numbers live in account_name. This endpoint lets a
    CFO/FM repair the number for a row so FNB pulls and transfers can
    address it correctly. Audit-logged.
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, ba_id):
        # Fable H4: this repoints which real-world bank account a row addresses
        # (so FNB debits/pulls follow it) — gate to Finance/CFO, and actually
        # write the audit row the docstring promised (it did neither before).
        if not _can_manage_fnb(request.user):
            return Response({'detail': 'Only Finance leadership / the CFO can change a '
                             'bank account number.'}, status=status.HTTP_403_FORBIDDEN)
        ba = get_object_or_404(BankAccount, pk=ba_id)
        new_num = (request.data or {}).get('account_number', '').strip()
        if not new_num:
            return Response({'detail': 'account_number is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not new_num.isdigit() or len(new_num) > 32:
            return Response({'detail': 'account_number must be 1-32 digits.'},
                            status=status.HTTP_400_BAD_REQUEST)
        old = ba.account_number
        ba.account_number = new_num
        ba.save(update_fields=['account_number'])
        FNBSyncLog.objects.create(
            direction      = FNBSyncLog.Direction.OUTBOUND,
            service        = FNBSyncLog.Service.TEST,
            http_method    = 'PATCH',
            endpoint       = f'/banking/bank-accounts/{ba.id}/set-account-number/',
            status         = FNBSyncLog.Status.SUCCESS,
            request_summary= (f'BankAccount {ba.id} account_number changed '
                              f'{old!r} -> {new_num!r} by {request.user.username}'),
        )
        return Response({
            'success':         True,
            'bank_account_id': str(ba.id),
            'old':             old,
            'new':             new_num,
        })


class BankAccountToggleHideView(APIView):
    """PATCH /api/v1/banking/bank-accounts/<id>/toggle-hide/

    Per CFO directive 2026-05-25 the FM wants to hide accounts they
    don't care about from the FNB Integration page (e.g. E-Wallet Pro
    Chimidza) without deactivating them globally. Body:
      {"hide": true}   -> hide
      {"hide": false}  -> unhide
    Defaults to flipping the current value if `hide` is omitted.

    Gated like its sibling set-account-number: any signed-in staffer could hide
    any company's paying account from the picker (Fable 5.1 audit 2026-09-02, M7).
    """
    permission_classes = [CanManageFNB]

    def patch(self, request, ba_id):
        ba = get_object_or_404(BankAccount, pk=ba_id)
        raw = (request.data or {}).get('hide', None)
        if raw is None:
            new_val = not bool(ba.hide_in_banking_ui)
        else:
            new_val = bool(raw) if not isinstance(raw, str) else raw.lower() in (
                '1', 'true', 'yes', 'on',
            )
        ba.hide_in_banking_ui = new_val
        ba.save(update_fields=['hide_in_banking_ui'])
        return Response({
            'success':            True,
            'bank_account_id':    str(ba.id),
            'hide_in_banking_ui': new_val,
        })


class FNBWebhookReceiverView(APIView):
    """POST /api/v1/fnb/webhook/

    FNB's signature header name is TBD — configurable via FNB_WEBHOOK_HEADER
    setting (default 'X-FNB-Signature').
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from django.conf import settings as _settings
        sig_header_name = getattr(_settings, 'FNB_WEBHOOK_HEADER', 'X-FNB-Signature')
        signature = request.headers.get(sig_header_name, '') or ''

        event = record_webhook(
            request.body or b'',
            dict(request.headers),
            signature_header=signature,
        )

        if event.status in (FNBWebhookEvent.Status.PROCESSED,
                            FNBWebhookEvent.Status.FAILED):
            # A redelivery of an event already handled — acknowledge it so the
            # bank stops resending; apply nothing twice (Fable 5.1 audit, M8).
            return Response({'received': True, 'event_id': str(event.pk),
                             'duplicate': True})
        if event.status == FNBWebhookEvent.Status.VERIFIED:
            dispatch_webhook(event)
            return Response({'received': True, 'event_id': str(event.pk)})

        # Not verified — accept but flag for review (don't 401 because that
        # makes some bank webhooks retry-storm forever)
        return Response(
            {'received': True, 'event_id': str(event.pk), 'verified': False},
            status=status.HTTP_202_ACCEPTED,
        )


# ---------------------------------------------------------------------------
# Exception cockpit — the daily three-way check, on a screen
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fnb_exception_cockpit(request):
    """GET /api/v1/fnb/exception-cockpit/  — where Omni and the bank disagree.

    Read only. It runs the same check the daily email runs, so the screen and
    the email can never tell two different stories (the two-parsers trap).

    Visible to anyone who may manage FNB or view financials; a payment queue
    nobody can open is a queue nobody owns, which is the problem this exists to
    fix.
    """
    from django.core.exceptions import PermissionDenied

    # The people the daily report is SENT to must be able to open the screen it
    # points at. Measured on prod the morning it went live: Kago Tshutlhedi
    # (Finance Manager) and Pako Kago (Financial Controller) both receive the
    # email and both got refused here - `_can_manage_fnb` is CFO/administrator
    # only. A queue nobody can open is exactly the unowned queue this exists to
    # fix. Gated on the report's OWN recipient list so the screen and the email
    # can never disagree about who this is for - one source of truth, the same
    # principle the check itself follows. (13-Sep-2026.)
    from .three_way_check_email import recipients as _report_recipients

    _email = (getattr(request.user, 'email', '') or '').strip().lower()
    _invited = bool(_email) and _email in {r.strip().lower() for r in _report_recipients()}
    if not (_can_manage_fnb(request.user) or request.user.is_superuser or _invited):
        raise PermissionDenied('You do not have access to the payments exception view.')

    from .three_way_check import FINDINGS, run

    try:
        stale = int(request.query_params.get('stale_days') or 7)
    except (TypeError, ValueError):
        return Response({'detail': 'stale_days must be a whole number of days.'},
                        status=400)
    stale = max(1, min(stale, 90))

    res = run(stale_days=stale)
    # Ship the wording with the data so the screen never re-invents its own
    # explanation of what a finding means.
    res['findings_meaning'] = {k: {'headline': h, 'what_it_means': m}
                               for k, (h, m) in FINDINGS.items()}
    return Response(res)
