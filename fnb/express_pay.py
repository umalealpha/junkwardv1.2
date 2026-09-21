"""fnb/express_pay.py — Express Pay: the CFO or CEO loads ONE payment to FNB from
the phone, single authorisation (CFO 2026-09-04, reaffirmed the same day:
"omni doesn't move physical money").

What this does and does not do, plainly:

  * It STAGES one payment into FNB's approval queue. Nothing leaves the bank
    here. The money moves only when the CFO releases the batch in the FNB app
    with his phone's two-factor — that release is the real second control.
  * It is the same mechanism as the (flag-disabled) quick transfer in
    ``fnb.api_views.FNBQuickTransferView``: a transient stand-in payment with
    NO ``created_by`` handed to the one choke point, ``submit_eft_batch``, with
    ``allow_single_person=True``. A persisted Payment stamped with its author
    would be REFUSED by ``_assert_release_is_a_second_person`` — so Express Pay
    deliberately writes no Payment row. The batch row (FNBBatchSubmission) and
    its sync log are the audit trail, exactly as for quick transfer.
  * It reuses the payment-request controls as WARNINGS the payer confirms to
    himself (PAY-BANK-01 changed account, PAY-BANK-03 never-paid payee, and a
    same-amount-same-account repeat in the last six hours). They are not a
    second approver — there is none by design — they stop a wrong account or
    a double load.

Everything money-related that submit_eft_batch enforces (RR10 charset, single
currency, the FNB_BATCH_MAX_BWP ceiling, the already-sent lock) stays inside
it and is surfaced verbatim.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
import logging
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsCfoOrCeo, is_cfo_or_ceo
from .client import FNBAPIError, FNBAuthError, FNBNotConfigured
from .models import ExpressPayee, FNBBatchSubmission
from .payments import _indeterminate, submit_eft_batch

log = logging.getLogger(__name__)

# A repeat of the same amount to the same account by the same person inside
# this window is almost always a double tap, so it asks before loading again.
DUPLICATE_WINDOW = timedelta(hours=6)
PAYMENT_NUMBER_PREFIX = 'EXP-'
FNB_BW_BIC = 'FIRNBWGX'
# The payee's bank is FNB when its name says so; any other bank needs a branch
# code, because build_batch_payload falls back to FNB's universal branch when
# the code is blank — right for FNB, a misroute for anyone else.
_FNB_NAMES = re.compile(r'\bfnb\b|first\s*national', re.I)


def _digits(s: str) -> str:
    return re.sub(r'\D', '', s or '')


def _batch_status_word(batch: FNBBatchSubmission) -> str:
    """loaded / paid / failed / unknown — the four words the phone shows."""
    S = FNBBatchSubmission.Status
    if batch.status == S.SETTLED:
        return 'paid'
    if batch.status in (S.PENDING, S.SUBMITTED, S.ACKNOWLEDGED):
        return 'loaded'
    if batch.status in (S.FAILED, S.CANCELLED):
        return 'failed'
    return 'unknown'


def _recent_duplicate(amount: Decimal, account_number: str):
    """A single-payment batch — by anyone — of the same amount to the same
    account in the last DUPLICATE_WINDOW, or None. Not scoped to the caller:
    the CFO and the CEO loading the same invoice is exactly the double load
    this exists to catch. The account lives inside the payload
    snapshot (paymentInformation[].creditTransferTransactionInformation[]
    .creditorAccount.accountNumber — the RMB EFT shape); compare on digits."""
    since = timezone.now() - DUPLICATE_WINDOW
    want = _digits(account_number)
    qs = (FNBBatchSubmission.objects
          .filter(payment_count=1, total_amount_bwp=amount, created_at__gte=since)
          .exclude(status__in=[FNBBatchSubmission.Status.FAILED,
                               FNBBatchSubmission.Status.CANCELLED])
          .order_by('-created_at'))
    for b in qs[:20]:
        for info in (b.payload_snapshot or {}).get('paymentInformation') or []:
            for tx in info.get('creditTransferTransactionInformation') or []:
                acct = ((tx.get('creditorAccount') or {}).get('accountNumber') or '')
                if _digits(acct) == want:
                    return b
    return None


class _Stand:
    """A throwaway stand-in. pk/id None so nothing stamps it as a real row;
    deliberately NO created_by — that is what makes a single-person release
    lawful inside submit_eft_batch (see the module docstring)."""
    pk = None
    id = None


class ExpressPayCanView(APIView):
    """GET /api/v1/fnb/express-pay/can/ → {allowed} — the phone shows or hides
    the tile. Any signed-in user may ask; the answer is the gate."""
    def get(self, request):
        return Response({'allowed': is_cfo_or_ceo(request.user)})


class ExpressPayView(APIView):
    """POST /api/v1/fnb/express-pay/

    Body: {payee_name, account_number, bank_name, branch_code?, amount,
           reference?, company?, category?, confirm?}

    company  — a Company code (e.g. ADIC). Defaults to ADIC, the licensed
               insurer, which is where the CFO's own payments come from.
    category — 'claim' pays from the claims account; anything else from the
               operating account (same map as the payment-request auto-load).
    confirm  — true re-posts past the warnings after the payer has read them.

    409 {needs_confirm:true, warnings:[...]} when a control fires and confirm
    was not given. 200 {loaded:true, ...} once the batch is in FNB's queue.
    """
    permission_classes = [IsCfoOrCeo]

    def post(self, request):
        from taskboard.fnb_autoload import _resolve_company, _resolve_source_account
        from taskboard.payee_bank_history import bank_change_warning, first_payment_warning

        b = request.data or {}
        payee = (b.get('payee_name') or '').strip()
        acct = (b.get('account_number') or '').strip()
        bank_name = (b.get('bank_name') or '').strip()
        branch = (b.get('branch_code') or '').strip()
        ref = (b.get('reference') or '').strip()
        company_code = (b.get('company') or 'ADIC').strip()
        category = (b.get('category') or 'operations').strip().lower()
        confirm = str(b.get('confirm', '')).lower() in ('1', 'true', 'yes')

        missing = [k for k, v in (('payee_name', payee), ('account_number', acct),
                                  ('bank_name', bank_name), ('amount', b.get('amount'))) if not v]
        if missing:
            return Response({'detail': f'Missing: {", ".join(missing)}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not _digits(acct):
            return Response({'detail': 'The account number needs digits.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            amount = Decimal(str(b.get('amount')).replace(',', '').replace(' ', '')).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError, TypeError):
            return Response({'detail': 'The amount must be a number.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({'detail': 'The amount must be more than zero.'},
                            status=status.HTTP_400_BAD_REQUEST)

        if not _digits(branch) and not _FNB_NAMES.search(bank_name):
            return Response(
                {'detail': (f'{bank_name} is not FNB, so its branch code is needed. Without '
                            'it the payment would be routed to an FNB branch and bounce, '
                            'or worse, land in the wrong place.')},
                status=status.HTTP_400_BAD_REQUEST)

        company = _resolve_company(company_code)
        if company is None:
            return Response({'detail': f'"{company_code}" is not a company on record.'},
                            status=status.HTTP_400_BAD_REQUEST)
        source_account = _resolve_source_account(company, 'claim' if category == 'claim' else '')
        if source_account is None:
            return Response(
                {'detail': (f'No paying account is configured for {company.code} '
                            f'({"claims" if category == "claim" else "operations"}). '
                            'Set PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS on the server.')},
                status=status.HTTP_400_BAD_REQUEST)

        # Warnings the payer confirms to himself — a wrong account or a double
        # load, not a second approver.
        warnings = []
        w = bank_change_warning(payee, acct)
        if w:
            warnings.append(w)
        w = first_payment_warning(payee, acct)
        if w:
            warnings.append(w)
        dup = _recent_duplicate(amount, acct)
        if dup is not None:
            by = dup.submitted_by
            who = (by.get_full_name() or by.username) if by else 'someone'
            warnings.append({
                'control': 'EXP-DUP',
                'payee': payee,
                'detail': (f'BWP {amount:,.2f} was already loaded to an account ending '
                           f'{_digits(acct)[-4:]} by {who} at '
                           f'{timezone.localtime(dup.created_at):%d %b %H:%M} '
                           f'(FNB reference {dup.fnb_reference or dup.idempotency_key}). '
                           'Loading it again would pay twice. Only continue if this is '
                           'a genuine second payment.'),
            })
        if warnings and not confirm:
            return Response({'needs_confirm': True, 'warnings': warnings},
                            status=status.HTTP_409_CONFLICT)
        overridden = [w['control'] for w in warnings]

        # The stand-ins — copied from FNBQuickTransferView, which already does
        # exactly this. No created_by, no DB row.
        vba = _Stand()
        vba.account_holder_name = payee
        vba.bank_name = bank_name
        vba.account_number = _digits(acct)
        vba.branch_code = _digits(branch)      # blank → FNB's own fallback branch
        vba.swift_bic = FNB_BW_BIC
        vba.account_type = 'CACC'
        vba.email = None

        p = _Stand()
        p.payment_number = f'{PAYMENT_NUMBER_PREFIX}{uuid.uuid4().hex[:16].upper()}'
        p.amount = amount
        p.amount_bwp = amount
        p.currency_code_id = 'BWP'
        p.description = ref or f'Express Pay {payee}'
        p.reference = ref
        p.vendor_bank_account = vba
        p.remittance_email = None
        label = f'{payee} {ref}'.strip()
        p.bank_our_reference = label
        p.bank_narration = label

        # NOT wrapped in atomic(): submit_eft_batch is two-phase and records its
        # own outcome around the bank call (CFO/Kago 2026-07-16 incident).
        try:
            batch = submit_eft_batch(
                [p], source_account=source_account, user=request.user,
                service_level_code='SDVA', allow_single_person=True)
        except FNBNotConfigured as e:
            return Response({'loaded': False, 'reason': 'not_configured', 'detail': str(e)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except FNBAuthError as e:
            return Response({'loaded': False, 'reason': 'bank_signin_failed',
                             'detail': ('Omni could not sign in to FNB, so nothing was '
                                        f'loaded: {str(e)[:200]}')},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except FNBAPIError as e:
            if _indeterminate(e):
                # The POST left and no clean answer came back. submit_eft_batch has
                # marked the batch UNKNOWN and the payment MAY be staged at FNB.
                # Never say "not loaded" here — that invites a second tap, which
                # mints a fresh key FNB cannot dedupe. Hand back the batch so the
                # CFO can check FNB first.
                unknown = (FNBBatchSubmission.objects
                           .filter(submitted_by=request.user, payment_count=1,
                                   total_amount_bwp=amount,
                                   status=FNBBatchSubmission.Status.UNKNOWN)
                           .order_by('-created_at').first())
                return Response({
                    'loaded': None, 'reason': 'unknown',
                    'batch_id': str(unknown.id) if unknown else None,
                    'reference': ((unknown.fnb_reference or unknown.idempotency_key)
                                  if unknown else ''),
                    'detail': ('FNB did not answer, so Omni cannot tell whether this payment '
                               'reached the bank. Open the FNB app and look for it BEFORE you '
                               'try again — loading it twice would pay twice.'),
                }, status=status.HTTP_502_BAD_GATEWAY)
            return Response({'loaded': False, 'reason': 'fnb_error',
                             'http_status': e.status_code, 'detail': (e.body or '')[:300]},
                            status=status.HTTP_502_BAD_GATEWAY)
        except ValidationError as e:
            msgs = getattr(e, 'messages', None) or [str(e)]
            return Response({'loaded': False, 'reason': 'validation', 'detail': ' '.join(msgs)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:                                  # noqa: BLE001
            log.exception('Express Pay load failed')
            return Response({'loaded': False, 'reason': 'error', 'detail': str(exc)[:300]},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            ExpressPayee.objects.filter(is_active=True, account_number=_digits(acct)).update(
                last_paid_at=timezone.now())
        except Exception:                                         # noqa: BLE001
            # Bookkeeping only. The payment IS staged — an error here must never
            # reach the phone as a failure, or the CFO taps Load again.
            log.exception('Express Pay: last_paid_at stamp failed')

        if overridden:
            # The payer waved a control through. Allowed here by design (there is
            # no second approver), but it must leave a trace.
            try:
                from core.models import AuditLog
                AuditLog.objects.create(
                    table_name='fnb.FNBBatchSubmission', record_id=str(batch.id),
                    action=AuditLog.Action.UPDATE, user=request.user,
                    new_values={'express_pay': True, 'controls_overridden': overridden,
                                'payee': payee, 'account_tail': _digits(acct)[-4:],
                                'amount_bwp': str(amount), 'warnings': warnings},
                    description=(f'Express Pay: {request.user.username} confirmed past '
                                 f'{", ".join(overridden)} and loaded BWP {amount:,.2f} to '
                                 f'{payee} (account ending {_digits(acct)[-4:]}).'))
            except Exception:                                     # noqa: BLE001
                log.exception('Express Pay: audit row for an overridden control was not written')

        return Response({
            'loaded': True,
            'batch_id': str(batch.id),
            'reference': batch.fnb_reference or batch.idempotency_key,
            'status': _batch_status_word(batch),
            'amount': str(amount),
            'payee': payee,
            'company': company.code,
            'next': 'Open your FNB app and approve it.',
        })


class ExpressPayStatusView(APIView):
    """GET /api/v1/fnb/express-pay/<batch_id>/status/ → {status, ...}.
    Read-only: loaded (in FNB's queue) / paid (settled) / failed / unknown."""
    permission_classes = [IsCfoOrCeo]

    def get(self, request, batch_id):
        batch = FNBBatchSubmission.objects.filter(pk=batch_id, submitted_by=request.user).first()
        if batch is None:
            return Response({'detail': 'No such load.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'batch_id': str(batch.id),
            'status': _batch_status_word(batch),
            'fnb_status': batch.status,
            'reference': batch.fnb_reference or batch.idempotency_key,
            'amount': str(batch.total_amount_bwp),
            'failure_reason': batch.failure_reason or '',
            'settled_at': batch.settled_at,
        })


class RecentPayeesView(APIView):
    """GET /api/v1/fnb/recent-payees/?q= → {payees:[{payee, account_number,
    bank_name, branch_code, source}]} — distinct payees from submitted payment
    requests, each with the bank we last paid them into (EXACT name match, so a
    look-alike supplier's account is never offered). Cap 20."""
    permission_classes = [IsCfoOrCeo]

    def get(self, request):
        from taskboard.models import PaymentRequest
        from taskboard.payee_bank_history import last_known_bank, normalise_payee

        q = (request.query_params.get('q') or '').strip()
        qs = (PaymentRequest.objects
              .exclude(status=PaymentRequest.Status.DRAFT)
              .exclude(payee='')
              .order_by('-created_at'))
        if q:
            qs = qs.filter(payee__icontains=q)
        seen, out = set(), []
        for payee in qs.values_list('payee', flat=True)[:400]:
            key = normalise_payee(payee)
            if not key or key in seen:
                continue
            seen.add(key)
            known = last_known_bank(payee, exact=True) or {}
            out.append({
                'payee': payee,
                'account_number': known.get('account_number', '') or '',
                'bank_name': known.get('bank_name', '') or '',
                'branch_code': known.get('branch_code', '') or '',
                'source': known.get('source_label', '') or '',
            })
            if len(out) >= 20:
                break
        return Response({'payees': out})


class ClaimPayeeView(APIView):
    """GET /api/v1/fnb/claim-payee/?claim=<ref> → who we last paid on that claim.

    Claim payouts live in Graphite, so Omni holds no claimant bank on a claim.
    The best Omni can do honestly: the most recent CLAIM payment request that
    mentions the reference, plus the bank we last paid that payee into.
    {found:false} otherwise — never a guess.
    """
    permission_classes = [IsCfoOrCeo]

    def get(self, request):
        from taskboard.models import PaymentRequest
        from taskboard.payee_bank_history import last_known_bank

        claim = (request.query_params.get('claim') or '').strip()
        if len(claim) < 3:
            return Response({'found': False, 'detail': 'Type at least 3 characters of the claim number.'})
        pr = (PaymentRequest.objects
              .filter(category=PaymentRequest.Category.CLAIM)
              .exclude(status=PaymentRequest.Status.DRAFT)
              .filter(Q(ref__icontains=claim) | Q(graphite_ref__icontains=claim)
                      | Q(subject__icontains=claim) | Q(payee__icontains=claim))
              .order_by('-created_at')
              .first())
        if pr is None:
            return Response({'found': False})
        known = last_known_bank(pr.payee, exact=True) or {}
        return Response({
            'found': True,
            'request_ref': pr.ref,
            'payee': pr.payee,
            'last_paid': str(pr.total),   # information only — the phone must NOT prefill the amount
            'account_number': known.get('account_number') or (pr.account_number or ''),
            'bank_name': known.get('bank_name') or (pr.bank_name or ''),
            'branch_code': known.get('branch_code') or (pr.branch_code or ''),
        })


# ---------------------------------------------------------------------------
# Saved payees — the "click and pay" list (CFO 2026-09-04)
# ---------------------------------------------------------------------------
def _payee_json(x: ExpressPayee) -> dict:
    return {
        'id': str(x.id), 'name': x.name, 'bank_name': x.bank_name,
        'account_number': x.account_number, 'branch_code': x.branch_code,
        'default_amount': str(x.default_amount) if x.default_amount is not None else '',
        'reference': x.reference, 'company': x.company_code, 'category': x.category,
        'note': x.note, 'last_paid_at': x.last_paid_at,
    }


def _read_payee_fields(b) -> tuple[dict | None, str]:
    if not isinstance(b, dict):
        return None, 'Send the payee as an object.'
    if any(isinstance(b.get(k), (list, dict)) for k in
           ('name', 'payee_name', 'account_number', 'bank_name', 'branch_code', 'default_amount',
            'reference', 'company', 'category', 'note')):
        return None, 'Each field must be plain text or a number.'
    g = lambda k, d='': str(b.get(k) if b.get(k) is not None else d)   # noqa: E731 — lists/dicts → 400, not 500
    name = (g('name') or g('payee_name')).strip()
    acct = _digits(g('account_number'))
    bank = g('bank_name', 'FNB').strip() or 'FNB'
    branch = _digits(g('branch_code'))
    if not name:
        return None, 'A name is needed.'
    if len(acct) < 6:
        return None, 'The account number needs at least 6 digits.'
    if not branch and not _FNB_NAMES.search(bank):
        return None, f'{bank} is not FNB, so its branch code is needed.'
    amt = None
    raw = g('default_amount').replace(',', '').replace(' ', '')
    if raw:
        try:
            amt = Decimal(raw).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            return None, 'The usual amount must be a number.'
        if amt <= 0:
            return None, 'The usual amount must be more than zero.'
    category = g('category', 'operations').strip().lower()
    return {
        'name': name[:140], 'bank_name': bank[:120], 'account_number': acct[:32], 'branch_code': branch[:16],
        'default_amount': amt, 'reference': g('reference').strip()[:120],
        'company_code': (g('company', 'ADIC').strip().upper() or 'ADIC')[:12],
        'category': 'claim' if category == 'claim' else 'operations',
        'note': g('note').strip()[:200],
    }, ''


class ExpressPayeeListView(APIView):
    """GET  /api/v1/fnb/express-pay/payees/  → {payees:[...]}  (active, by name)
       POST /api/v1/fnb/express-pay/payees/  → save one (or update the one with the
       same account number, so re-saving never makes a duplicate)."""
    permission_classes = [IsCfoOrCeo]

    def get(self, request):
        qs = ExpressPayee.objects.filter(is_active=True).order_by('name')
        return Response({'payees': [_payee_json(x) for x in qs]})

    def post(self, request):
        fields, err = _read_payee_fields(request.data or {})
        if fields is None:
            return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)
        existing = ExpressPayee.objects.filter(account_number=fields['account_number']).first()
        if existing is not None:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.is_active = True
            existing.save()
            return Response(_payee_json(existing))
        x = ExpressPayee.objects.create(created_by=request.user, **fields)
        return Response(_payee_json(x), status=status.HTTP_201_CREATED)


class ExpressPayeeDetailView(APIView):
    """PATCH /api/v1/fnb/express-pay/payees/<id>/ (edit) · DELETE (remove from the list)."""
    permission_classes = [IsCfoOrCeo]

    def _get(self, pk):
        return ExpressPayee.objects.filter(pk=pk, is_active=True).first()

    def patch(self, request, pk):
        x = self._get(pk)
        if x is None:
            return Response({'detail': 'No such saved payee.'}, status=status.HTTP_404_NOT_FOUND)
        if not isinstance(request.data, dict):
            return Response({'detail': 'Send the changes as an object.'}, status=status.HTTP_400_BAD_REQUEST)
        merged = {**_payee_json(x), 'company': x.company_code, **request.data}
        # A PATCH may not move this payee onto another saved payee's account.
        fields_probe, _ = _read_payee_fields(merged)
        if fields_probe and ExpressPayee.objects.filter(account_number=fields_probe['account_number']).exclude(pk=x.pk).exists():
            return Response({'detail': 'Another saved payee already has that account number.'}, status=status.HTTP_400_BAD_REQUEST)
        fields, err = _read_payee_fields(merged)
        if fields is None:
            return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)
        for k, v in fields.items():
            setattr(x, k, v)
        x.save()
        return Response(_payee_json(x))

    def delete(self, request, pk):
        x = self._get(pk)
        if x is None:
            return Response({'detail': 'No such saved payee.'}, status=status.HTTP_404_NOT_FOUND)
        x.is_active = False
        x.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Payment history — "search old payments by date, name or amount and pay again"
# (CFO 2026-09-04)
# ---------------------------------------------------------------------------
class PaymentHistoryView(APIView):
    """GET /api/v1/fnb/express-pay/history/?q=&from=&to=&amount=&limit=

    Past payments Omni knows about, newest first, each with the bank we paid
    into so the phone can fill Express Pay with one tap:
      * payment requests (every category, not drafts) — payee, total, reference,
        the account typed on the request (or the bank we last paid that payee);
      * earlier Express Pay loads (single-payment FNB batches) — payee and account
        from the batch payload.
    q      — payee / reference / subject contains (case-insensitive)
    from,to — YYYY-MM-DD on the payment's date
    amount — exact BWP amount (2 dp), or a range as amount_min / amount_max
    Read-only; nothing here moves money.
    """
    permission_classes = [IsCfoOrCeo]

    @staticmethod
    def _dec(v):
        try:
            return Decimal(str(v).replace(',', '').replace(' ', '')).quantize(Decimal('0.01')) if v not in (None, '') else None
        except (InvalidOperation, ValueError):
            return None

    def get(self, request):
        from datetime import date as _date
        from taskboard.models import PaymentRequest
        from taskboard.payee_bank_history import last_known_bank

        qp = request.query_params
        q = (qp.get('q') or '').strip()
        amount = self._dec(qp.get('amount'))
        amount_min = self._dec(qp.get('amount_min'))
        amount_max = self._dec(qp.get('amount_max'))
        try:
            d_from = _date.fromisoformat(qp['from']) if qp.get('from') else None
            d_to = _date.fromisoformat(qp['to']) if qp.get('to') else None
        except ValueError:
            return Response({'detail': 'Dates must be YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = max(1, min(int(qp.get('limit') or 50), 200))
        except ValueError:
            limit = 50

        rows = []

        prs = (PaymentRequest.objects
               .exclude(status=PaymentRequest.Status.DRAFT)
               .exclude(payee='')
               .order_by('-created_at'))
        if q:
            prs = prs.filter(Q(payee__icontains=q) | Q(ref__icontains=q) | Q(subject__icontains=q))
        if d_from:
            prs = prs.filter(created_at__date__gte=d_from)
        if d_to:
            prs = prs.filter(created_at__date__lte=d_to)
        if amount is not None:
            prs = prs.filter(total=amount)
        if amount_min is not None:
            prs = prs.filter(total__gte=amount_min)
        if amount_max is not None:
            prs = prs.filter(total__lte=amount_max)
        for pr in prs[:limit]:
            acct = (pr.account_number or '').strip()
            bank = (pr.bank_name or '').strip()
            branch = (pr.branch_code or '').strip()
            if not acct:
                known = last_known_bank(pr.payee, exact=True) or {}
                acct, bank, branch = known.get('account_number', ''), known.get('bank_name', ''), known.get('branch_code', '')
            rows.append({
                'source': 'payment_request', 'id': str(pr.id), 'ref': pr.ref,
                'at': pr.created_at.isoformat(),
                'date': timezone.localtime(pr.created_at).date().isoformat(),
                'payee': pr.payee, 'amount': str(pr.total), 'currency': pr.currency or 'BWP',
                'reference': pr.subject or pr.ref, 'status': pr.status,
                'category': 'claim' if pr.category == PaymentRequest.Category.CLAIM else 'operations',
                'company': pr.entity or '',
                'account_number': acct or '', 'bank_name': bank or '', 'branch_code': branch or '',
            })

        # Earlier Express Pay loads (and quick transfers): one payment per batch,
        # payee + account live in the payload.
        batches = (FNBBatchSubmission.objects
                   .filter(payment_count=1)
                   .exclude(status__in=[FNBBatchSubmission.Status.CANCELLED, FNBBatchSubmission.Status.FAILED])
                   .order_by('-created_at'))
        if d_from:
            batches = batches.filter(created_at__date__gte=d_from)
        if d_to:
            batches = batches.filter(created_at__date__lte=d_to)
        if amount is not None:
            batches = batches.filter(total_amount_bwp=amount)
        if amount_min is not None:
            batches = batches.filter(total_amount_bwp__gte=amount_min)
        if amount_max is not None:
            batches = batches.filter(total_amount_bwp__lte=amount_max)
        for b in batches[:limit]:
            tx = None
            for info in (b.payload_snapshot or {}).get('paymentInformation') or []:
                for t in info.get('creditTransferTransactionInformation') or []:
                    tx = t
                    break
                if tx:
                    break
            if not tx:
                continue
            payee = ((tx.get('creditor') or {}).get('name') or '').strip()
            narr = (tx.get('remittanceInformationUnstructured') or '').strip()
            if q and q.lower() not in f'{payee} {narr} {b.idempotency_key}'.lower():
                continue
            # Only Express Pay / quick-transfer loads: a payment-request load is already
            # listed above (its endToEndId carries the PAY/ reference).
            if 'PAY/' in (tx.get('endToEndId') or '').upper():
                continue
            rows.append({
                'source': 'express_pay', 'id': str(b.id), 'ref': b.fnb_reference or b.idempotency_key,
                'at': b.created_at.isoformat(),
                'date': timezone.localtime(b.created_at).date().isoformat(),
                'payee': payee, 'amount': str(b.total_amount_bwp), 'currency': b.currency_code or 'BWP',
                'reference': narr, 'status': b.status, 'category': 'operations', 'company': '',
                'account_number': (tx.get('creditorAccount') or {}).get('accountNumber') or '',
                'bank_name': 'FNB' if ((tx.get('creditorAgent') or {}).get('branchId') or '') in ('', '287867') else '',
                'branch_code': (tx.get('creditorAgent') or {}).get('branchId') or '',
            })

        rows.sort(key=lambda r: r['at'], reverse=True)
        return Response({'payments': rows[:limit]})
