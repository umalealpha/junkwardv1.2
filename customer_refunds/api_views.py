"""
customer_refunds/api_views.py

Endpoints (all under /api/v1/):

  POST customer-refunds/inbound/         Graphite → Omni handoff (shared-token auth)
  GET  customer-refunds/queue/           Finance queue (segment-scoped)
  GET  customer-refunds/<pk>/            one refund (segment-scoped)
  POST customer-refunds/<pk>/approve/    one Finance approver → raises Payment
  POST customer-refunds/<pk>/reject/     Finance rejects (reason)
  POST customer-refunds/<pk>/load-to-fnb/  gated: preview | live send
  POST customer-refunds/<pk>/mark-paid/  mark PAID → post back to Graphite

Two separate AREAS, different teams (CFO 2026-07-24):
  * MIS (UniCoin) — group `refund_area_mis`
  * Domestic & Commercial — group `refund_area_dc`
UniCoin staff must NOT see D&C refunds and vice versa. Money movers also need
Finance authority (_can_manage_fnb). Superuser / CFO see all.
"""
from __future__ import annotations

import hmac
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from fnb.api_views import _can_manage_fnb

from .ai_fraud import deepseek_fraud_review
from .fraud import apply_scan
from .models import CustomerRefund
from .services import (
    RefundConfigError,
    create_refund_payment,
    fnb_send_enabled,
    load_refund_to_fnb,
    post_refund_back_to_graphite,
)

MIS = CustomerRefund.Segment.MIS
DC = {CustomerRefund.Segment.DOMESTIC, CustomerRefund.Segment.COMMERCIAL}


# Amount above which a refund needs CFO / administrator approval (not a mere
# reviewer). This is a HARD hold, separate from the fraud "structuring" flag.
CFO_APPROVAL_THRESHOLD = 50000


def _reviewer_segments(user) -> set:
    """Segments the user may REVIEW (approve/reject/escalate).
    Reviewer groups: refund_area_mis / refund_area_dc. CFO/admin/superuser → all.
    """
    if user.is_superuser or _can_manage_fnb(user):
        return {MIS} | DC
    groups = set(user.groups.values_list('name', flat=True))
    allowed = set()
    if 'refund_area_mis' in groups:
        allowed.add(MIS)
    if 'refund_area_dc' in groups:
        allowed |= DC
    return allowed


def _input_segments(user) -> set:
    """Segments the user may INPUT/view only (raises refunds, cannot approve).
    Inputter groups: refund_input_mis / refund_input_dc.
    """
    groups = set(user.groups.values_list('name', flat=True))
    allowed = set()
    if 'refund_input_mis' in groups:
        allowed.add(MIS)
    if 'refund_input_dc' in groups:
        allowed |= DC
    return allowed


def _allowed_segments(user) -> set:
    """Segments the user may SEE (reviewer OR inputter OR admin)."""
    return _reviewer_segments(user) | _input_segments(user)


def _can_review(user, segment) -> bool:
    """May this user approve/reject/escalate refunds in this segment?
    Reviewers + administrators — NOT inputter-only users (SoD)."""
    return segment in _reviewer_segments(user)


def _can_move_money(user) -> bool:
    """May this user move refund money (send to FNB, >P50k approve, override,
    manual mark-paid)? CFO / admin, OR the refund money-authority group
    `refund_money` (Keetile / Kago / Pako per CFO 2026-07-27). Scoped to
    refunds — does NOT widen the general FNB money gate."""
    return _can_manage_fnb(user) or user.groups.filter(name='refund_money').exists()


def _serialize(r: CustomerRefund) -> dict:
    """Never emit the full account number — last-4 only (PII)."""
    return {
        'id': str(r.pk),
        'segment': r.segment,
        'graphite_ref': r.graphite_ref,
        'policy_number': r.policy_number,
        'product_name': r.product_name,
        'customer_name': r.customer_name,
        'agent_name': r.agent_name,
        'reason': r.reason,
        'refund_amount': str(r.refund_amount),
        'currency': r.currency,
        'bank_name': r.bank_name,
        'branch_code': r.branch_code,
        'account_last4': r.account_last4,
        'ai_greenlight': r.ai_greenlight,
        'fraud_flags': r.fraud_flags,
        'fraud_score': r.fraud_score,
        'ai_fraud_review': r.ai_fraud_review,
        'status': r.status,
        'finance_approved_at': r.finance_approved_at,
        'reject_reason': r.reject_reason,
        'payment_id': str(r.payment_id) if r.payment_id else None,
        'fnb_batch_id': str(r.fnb_batch_id) if r.fnb_batch_id else None,
        'paid_at': r.paid_at,
        'graphite_posted': r.graphite_posted,
        'created_at': r.created_at,
    }


class RefundInboundView(APIView):
    """POST customer-refunds/inbound/ — Graphite hands a green-lit, approved
    refund to Omni. Auth = shared bearer token (REFUND_INBOUND_TOKEN), server-
    to-server. Idempotent on graphite_ref.
    """
    # MUST stay empty. With AZURE_SSO_ENABLED=true (prod) AzureJWTAuthentication
    # raises on any non-JWT Bearer value, so DRF returned 403 "Bad token header:
    # Not enough segments" before this view ran — Graphite's handoff could never
    # authenticate, whatever token it sent. Found on prod 2026-07-25 by calling
    # the live endpoint; the earlier "verified live (401/403 on the auth walls)"
    # had mistaken that 403 for a working wall. The token check below is the wall.
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        expected = getattr(settings, 'REFUND_INBOUND_TOKEN', '') or ''
        got = (request.headers.get('Authorization') or '').replace('Bearer ', '').strip()
        if not expected or not hmac.compare_digest(got, expected):
            return Response({'detail': 'Unauthorised.'}, status=status.HTTP_401_UNAUTHORIZED)

        d = request.data or {}
        ref = str(d.get('graphite_ref') or '').strip()
        policy = str(d.get('policy_number') or '').strip()
        if not ref or not policy:
            return Response({'detail': 'graphite_ref and policy_number are required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        segment = str(d.get('segment') or MIS).strip().lower()
        if segment not in CustomerRefund.Segment.values:
            return Response({'detail': f'segment must be one of '
                             f'{CustomerRefund.Segment.values}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            amount = Decimal(str(d.get('refund_amount') or '0')).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError):
            return Response({'detail': 'refund_amount must be numeric.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({'detail': 'refund_amount must be > 0.'},
                            status=status.HTTP_400_BAD_REQUEST)

        currency = str(d.get('currency') or 'BWP').upper().strip()
        if currency != 'BWP':
            return Response({'detail': 'Only BWP refunds are supported.'},
                            status=status.HTTP_400_BAD_REQUEST)

        existing = CustomerRefund.objects.filter(graphite_ref=ref).first()
        if existing:
            return Response({'detail': 'Already received.', 'refund': _serialize(existing)},
                            status=status.HTTP_200_OK)

        r = CustomerRefund(
            segment=segment,
            graphite_ref=ref,
            policy_number=policy,
            product_name=str(d.get('product_name') or '')[:191],
            customer_name=str(d.get('customer_name') or '')[:191],
            agent_name=str(d.get('agent_name') or '')[:191],
            reason=str(d.get('reason') or '')[:191],
            refund_amount=amount,
            currency=currency,
            bank_name=str(d.get('bank_name') or '')[:120],
            branch_name=str(d.get('branch_name') or '')[:120],
            branch_code=str(d.get('branch_code') or '')[:20],
            ai_greenlight=bool(d.get('ai_greenlight', False)),
            ai_evidence=d.get('ai_evidence') or {},
            status=CustomerRefund.Status.FINANCE_QUEUE,
        )
        r.set_account_number(str(d.get('account_number') or ''))
        try:
            r.save()
        except IntegrityError:
            # Duplicate graphite_ref race — idempotent success.
            existing = CustomerRefund.objects.filter(graphite_ref=ref).first()
            return Response({'detail': 'Already received.', 'refund': _serialize(existing)},
                            status=status.HTTP_200_OK)
        # Auto fraud scan on intake (best-effort — never block the handoff).
        try:
            apply_scan(r)
        except Exception:  # noqa: BLE001 — scan must not fail the receive
            pass
        # Auto-stage for the CFO (CFO 2026-07-28): raise the payment, load to FNB
        # (preview until REFUND_FNB_SEND_ENABLED is on) and task + email the
        # CFO to authorise in FNB. The CFO does nothing in Omni. Best-effort — a
        # staging problem leaves the refund in FINANCE_QUEUE, never fails receipt.
        try:
            from .services import stage_handoff_refund
            stage_handoff_refund(r)
        except Exception:  # noqa: BLE001
            pass
        return Response({'detail': 'Received.', 'refund': _serialize(r)},
                        status=status.HTTP_201_CREATED)


class _FinanceView(APIView):
    """Base: authenticated + access to at least one refund area. Workflow
    actions are gated by AREA (MIS vs D&C). The actual money-out (live FNB
    send) additionally requires money authority — enforced in the live path.
    """
    permission_classes = [IsAuthenticated]

    def _guard(self, request, refund=None):
        allowed = _allowed_segments(request.user)
        if not allowed:
            return Response({'detail': 'You are not assigned to a refund area '
                             '(MIS or Domestic & Commercial).'},
                            status=status.HTTP_403_FORBIDDEN)
        if refund is not None and refund.segment not in allowed:
            return Response({'detail': 'You do not have access to this segment '
                             '(UniCoin/MIS and D&C are separate).'},
                            status=status.HTTP_403_FORBIDDEN)
        return None


class RefundQueueView(_FinanceView):
    def get(self, request):
        block = self._guard(request)
        if block:
            return block
        allowed = _allowed_segments(request.user)
        st = request.query_params.get('status') or CustomerRefund.Status.FINANCE_QUEUE
        qs = CustomerRefund.objects.filter(status=st, segment__in=allowed)
        seg = request.query_params.get('segment')
        if seg:
            qs = qs.filter(segment=seg)
        if str(request.query_params.get('flagged') or '').lower() in ('1', 'true', 'yes'):
            qs = qs.filter(fraud_score__gt=0).order_by('-fraud_score')
        qs = qs[:500]
        return Response({'count': len(qs), 'results': [_serialize(r) for r in qs]})


class RefundDetailView(_FinanceView):
    def get(self, request, pk):
        r = get_object_or_404(CustomerRefund, pk=pk)
        block = self._guard(request, r)
        if block:
            return block
        return Response(_serialize(r))


class RefundFraudScanView(_FinanceView):
    """POST customer-refunds/<pk>/fraud-scan/ — re-run the fraud checks."""
    def post(self, request, pk):
        r = get_object_or_404(CustomerRefund, pk=pk)
        block = self._guard(request, r)
        if block:
            return block
        res = apply_scan(r)
        return Response({'fraud_score': res['score'], 'fraud_flags': res['flags'],
                         'refund': _serialize(r)})


class RefundAiFraudView(_FinanceView):
    """POST customer-refunds/<pk>/ai-fraud-review/ — DeepSeek advisory opinion
    over the rule-based signals (PII-free input). Advisory only — does not
    block; the rule engine already gates CRITICAL cases."""
    def post(self, request, pk):
        r = get_object_or_404(CustomerRefund, pk=pk)
        block = self._guard(request, r)
        if block:
            return block
        apply_scan(r)                       # freshest signals feed the reviewer
        review = deepseek_fraud_review(r)
        return Response({'ai_fraud_review': review, 'refund': _serialize(r)})


class RefundApproveView(_FinanceView):
    """One Finance approver signs off → raises the once-off Payment (no GL)."""
    def post(self, request, pk):
        with transaction.atomic():
            r = get_object_or_404(
                CustomerRefund.objects.select_for_update(), pk=pk)
            block = self._guard(request, r)
            if block:
                return block
            # Reviewer/admin only — an inputter (e.g. Phatsimo) cannot approve (SoD).
            if not _can_review(request.user, r.segment):
                return Response({'detail': 'You can raise refunds but not approve them '
                                 '(separation of duties).'}, status=status.HTTP_403_FORBIDDEN)
            # Over P50,000 → CFO / administrator approval, not a plain reviewer.
            if float(r.refund_amount or 0) > CFO_APPROVAL_THRESHOLD and not _can_move_money(request.user):
                return Response({'detail': f'Refunds over P{CFO_APPROVAL_THRESHOLD:,} need '
                                 'CFO / money-authority approval.'},
                                status=status.HTTP_403_FORBIDDEN)
            if r.status != CustomerRefund.Status.FINANCE_QUEUE:
                return Response({'detail': f'Refund is {r.status}, not awaiting Finance.'},
                                status=status.HTTP_400_BAD_REQUEST)
            if r.payment_id:
                return Response({'detail': 'Payment already raised.'},
                                status=status.HTTP_400_BAD_REQUEST)
            # Fraud gate: a CRITICAL flag blocks approval unless a senior
            # explicitly overrides with a written reason (recorded).
            apply_scan(r)
            critical = [f for f in r.fraud_flags if f.get('severity') == 'CRITICAL']
            body = request.data or {}
            override = bool(body.get('override_fraud'))
            if critical and not override:
                return Response(
                    {'detail': 'Blocked — fraud flags on this refund. A senior must '
                     'review, then resend with override_fraud=true and a reason.',
                     'fraud_flags': r.fraud_flags},
                    status=status.HTTP_409_CONFLICT)
            if critical and override:
                # Overriding a fraud flag is a money-authority act.
                if not _can_move_money(request.user):
                    return Response({'detail': 'Only a money-authority user (CFO / '
                                     'Keetile / Kago / Pako) can override a fraud flag.'},
                                    status=status.HTTP_403_FORBIDDEN)
                if not str(body.get('reason') or '').strip():
                    return Response({'detail': 'Overriding a fraud flag needs a reason.'},
                                    status=status.HTTP_400_BAD_REQUEST)
            try:
                payment = create_refund_payment(r, request.user)
            except RefundConfigError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            r.payment = payment
            r.status = CustomerRefund.Status.APPROVED
            r.finance_approved_by = request.user
            r.finance_approved_at = timezone.now()
            update = ['payment', 'status', 'finance_approved_by',
                      'finance_approved_at', 'updated_at']
            if critical and override:
                ev = dict(r.ai_evidence or {})
                ev['fraud_override'] = {'by': request.user.username,
                                        'reason': str(body.get('reason')).strip(),
                                        'at': timezone.now().isoformat(),
                                        'codes': [f['code'] for f in critical]}
                r.ai_evidence = ev
                update.append('ai_evidence')
            r.save(update_fields=update)
        return Response({'detail': 'Approved; payment raised (not yet sent to FNB).',
                         'refund': _serialize(r)})


class RefundRejectView(_FinanceView):
    def post(self, request, pk):
        r = get_object_or_404(CustomerRefund, pk=pk)
        block = self._guard(request, r)
        if block:
            return block
        if not _can_review(request.user, r.segment):
            return Response({'detail': 'You can raise refunds but not reject them '
                             '(separation of duties).'}, status=status.HTTP_403_FORBIDDEN)
        # Only a queued refund may be rejected — never one already approved/paid.
        if r.status != CustomerRefund.Status.FINANCE_QUEUE:
            return Response({'detail': f'Refund is {r.status}; only a queued refund '
                             'can be rejected.'}, status=status.HTTP_400_BAD_REQUEST)
        reason = str((request.data or {}).get('reason') or '').strip()
        if not reason:
            return Response({'detail': 'A rejection reason is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        r.status = CustomerRefund.Status.REJECTED
        r.reject_reason = reason
        r.save(update_fields=['status', 'reject_reason', 'updated_at'])
        return Response({'detail': 'Rejected.', 'refund': _serialize(r)})


class RefundLoadToFnbView(_FinanceView):
    """GATED. Preview by default; live send only when ?live=1 AND the master
    switch REFUND_FNB_SEND_ENABLED is on AND the payment is confirmed."""
    def post(self, request, pk):
        r = get_object_or_404(CustomerRefund, pk=pk)
        block = self._guard(request, r)
        if block:
            return block
        if r.status not in (CustomerRefund.Status.APPROVED,
                            CustomerRefund.Status.FNB_LOADED):
            return Response({'detail': f'Refund is {r.status}; approve it first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        live = str(request.query_params.get('live') or '').lower() in ('1', 'true', 'yes')
        if live and not _can_move_money(request.user):
            return Response({'detail': 'Only a money-authority user (CFO / Keetile / '
                             'Kago / Pako) can send a payment to the bank.'},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            result = load_refund_to_fnb(r, request.user, live=live)
        except RefundConfigError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        result['fnb_send_enabled'] = fnb_send_enabled()
        result['refund'] = _serialize(r)
        return Response(result)


class RefundMarkPaidView(_FinanceView):
    """Mark PAID → post back to Graphite. Normally only after FNB load. A manual
    EFT (paid outside the FNB integration) needs manual=true + a reason."""
    def post(self, request, pk):
        r = get_object_or_404(CustomerRefund, pk=pk)
        block = self._guard(request, r)
        if block:
            return block
        body = request.data or {}
        manual = bool(body.get('manual'))
        if r.status == CustomerRefund.Status.FNB_LOADED:
            # LOADED to FNB is not PAID — nothing has left the bank until the
            # CFO authorises it there with 2-factor. Recording it paid (and
            # crediting the policy back in Graphite) is therefore the same
            # decision as a manual payment and needs the same authority and a
            # second person (Fable 5.1 audit 2026-09-02, M6: this branch
            # demanded nothing).
            if not _can_move_money(request.user):
                return Response({'detail': 'Only a money-authority user (CFO / Keetile '
                                 '/ Kago / Pako) can record a loaded refund as paid — '
                                 'loaded is not paid until the bank has authorised it.'},
                                status=status.HTTP_403_FORBIDDEN)
            if r.finance_approved_by_id and r.finance_approved_by_id == request.user.id:
                return Response({'detail': 'A different person must confirm payment '
                                 '(four-eyes).'}, status=status.HTTP_403_FORBIDDEN)
        elif r.status == CustomerRefund.Status.APPROVED and manual:
            # A manual EFT (paid outside the FNB integration) credits the policy
            # with no batch record — treat it like a money-mover: needs money
            # authority + four-eyes + a reason (Fable fix 3).
            if not _can_move_money(request.user):
                return Response({'detail': 'Only a money-authority user (CFO / Keetile '
                                 '/ Kago / Pako) can record a manual payment.'},
                                status=status.HTTP_403_FORBIDDEN)
            if r.finance_approved_by_id and r.finance_approved_by_id == request.user.id:
                return Response({'detail': 'A different person must confirm payment '
                                 '(four-eyes).'}, status=status.HTTP_403_FORBIDDEN)
            if not str(body.get('reason') or '').strip():
                return Response({'detail': 'A manual payment needs a reason.'},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({'detail': f'Refund is {r.status}; load it to FNB first '
                             '(or mark manual=true with a reason).'},
                            status=status.HTTP_400_BAD_REQUEST)
        r.status = CustomerRefund.Status.PAID
        r.paid_at = timezone.now()
        r.save(update_fields=['status', 'paid_at', 'updated_at'])
        callback = post_refund_back_to_graphite(r)
        return Response({'detail': 'Marked paid.', 'graphite_callback': callback,
                         'refund': _serialize(r)})
