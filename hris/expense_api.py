"""
hris/expense_api.py — employee expense-refund workflow (CFO directive 2026-07-13).

Kills the ~10-email refund chain. Flow:
  employee submits (invoices + >=50-word reason, picks an on-duty senior accountant,
  never themselves)  ->  that accountant loads the payment into FNB manually + uploads
  proof, OR rejects back to the requester  ->  CFO approves, OR rejects (needs detail)
  back to the requester. Both dashboards show the live status.

No FNB integration yet (manual load); a "push to FNB" button can come later.
No GL posting here — this is the approval + proof trail only.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import Group, User
from django.utils import timezone
from rest_framework import status as http
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import UserProfile, get_user_profile
from .expense_claim_models import ExpenseClaim, ExpenseClaimAttachment

APPROVER_GROUP = 'expense_approver'   # senior-accountant pool

_MAX_UPLOAD = 10 * 1024 * 1024        # 10 MB per file (matches bug-report / task-evidence caps)
_OK_UPLOAD_TYPES = ('image/', 'application/pdf')


def _check_uploads(files, *, max_files=12) -> str | None:
    """Validate a batch of uploaded receipts/proofs. Returns an error string
    (for a 400) or None if all good. Receipt snaps come straight off a phone
    camera, so we bound count, size and type (CFO 2026-07-14)."""
    if len(files) > max_files:
        return f'Please attach at most {max_files} files.'
    for f in files:
        if f.size > _MAX_UPLOAD:
            return f'"{f.name}" is too large — each file must be 10 MB or less.'
        ctype = (getattr(f, 'content_type', '') or '').lower()
        if ctype and not ctype.startswith(_OK_UPLOAD_TYPES):
            return f'"{f.name}" is not a photo or PDF.'
    return None


def _is_approver(user) -> bool:
    return bool(user and (user.is_superuser
                          or user.groups.filter(name=APPROVER_GROUP).exists()))


def _is_cfo(user) -> bool:
    prof = get_user_profile(user)
    return bool(user and (user.is_superuser
                          or (prof and prof.title == UserProfile.Title.CFO)))


def _expense_accounts(search: str = '', company: str = ''):
    """Selectable GL accounts for a refund = EXPENSE lines ONLY (CFO 2026-07-13).
    Deliberately excludes balance-sheet (asset/liability/equity), revenue/income,
    and reinsurance accounts — a refund is an operating expense, nothing else."""
    from ledger.models import Account
    from django.db.models import Q
    qs = (Account.objects.filter(is_active=True, account_type='expense')
          .exclude(name__icontains='reinsur').exclude(sub_type__icontains='reinsur'))
    # The chart of accounts is shared across every entity — ledger.Account has no
    # company field — so a company param is accepted for API compatibility but is
    # NOT used to filter (filtering by it here raised FieldError and 500'd).
    _ = (company or '').strip()
    search = (search or '').strip()
    if search:
        qs = qs.filter(Q(code__icontains=search) | Q(name__icontains=search))
    return qs.order_by('code')


def _claim_dict(c: ExpenseClaim) -> dict:
    return {
        'id': str(c.id),
        'requester': c.profile.user.get_full_name() or c.profile.user.username,
        'requester_id': c.profile.user_id,
        'expense_date': c.expense_date,
        'category': c.category,
        'amount': str(c.amount),
        'currency': c.currency,
        'description': c.description,
        'status': c.status,
        'status_label': c.get_status_display(),
        'approver': (c.approver.get_full_name() or c.approver.username) if c.approver else None,
        'approver_id': c.approver_id,
        'reject_reason': c.reject_reason,
        'paid_by': (getattr(c.paid_by, 'username', '') or ''),
        'paid_at': c.paid_at.isoformat() if c.paid_at else None,
        'paid_reference': c.paid_reference,
        'paid_note': c.paid_note,
        # File access goes through the authenticated download endpoint below —
        # raw /media/ URLs are NOT routed in production (DEBUG=False), so a
        # direct .url link 404s and the proof-before-approval control breaks.
        'invoice_count': sum(1 for i in c.invoices.all() if i.file),
        'has_payment_proof': bool(c.payment_proof),
        'gl_account_id': str(c.gl_account_id) if c.gl_account_id else None,
        'gl_account_label': (f'{c.gl_account.code} — {c.gl_account.name}'
                             if c.gl_account_id and c.gl_account else None),
        'submitted_at': c.submitted_at,
        'processed_at': c.processed_at,
        'approved_at': c.approved_at,
        'created_at': c.created_at,
    }


def _email_link(to_email: str, subject: str, line: str, claim: ExpenseClaim):
    if not to_email:
        return
    from django.conf import settings
    from core.notifications import send_html_with_cfo_cc
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw')
    html = (f'<p>{line}</p>'
            f'<p><b>{claim.profile.user.get_full_name()}</b> — {claim.currency} {claim.amount} '
            f'({claim.category or "refund"}).</p>'
            f'<p><a href="{base}/refunds">Open it in Omni</a></p>')
    try:
        send_html_with_cfo_cc(subject=subject, html=html, to=[to_email], cc_cfo=False)
    except Exception:   # noqa: BLE001 — never let a notification failure block the workflow
        pass


class ExpenseApproversView(APIView):
    """The senior-accountant pool the requester can pick from (excluding self)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        pool = (User.objects.filter(groups__name=APPROVER_GROUP, is_active=True)
                .exclude(pk=request.user.id).distinct().order_by('first_name'))
        return Response([{'id': u.id, 'name': u.get_full_name() or u.username} for u in pool])


class ExpenseClaimSubmitView(APIView):
    """One-click submit: create the claim + invoices, route to the chosen accountant."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        prof = get_user_profile(request.user)
        if prof is None:
            return Response({'detail': 'No profile on your account.'}, status=http.HTTP_400_BAD_REQUEST)
        d = request.data
        description = (d.get('description') or '').strip()
        if len(description.split()) < 50:
            return Response({'detail': 'Please describe the expense in at least 50 words '
                                       f'(you wrote {len(description.split())}).'},
                            status=http.HTTP_400_BAD_REQUEST)
        try:
            amount = Decimal(str(d.get('amount') or '0'))
            if not amount.is_finite():          # 'NaN' / 'Infinity' parse but aren't valid money
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return Response({'detail': 'Amount is not a valid number.'}, status=http.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({'detail': 'Amount must be greater than zero.'}, status=http.HTTP_400_BAD_REQUEST)
        # Parse the expense date up front so a bad string is a clean 400, not a 500 at save.
        raw_date = d.get('expense_date')
        if raw_date:
            from datetime import date as _date
            try:
                expense_date = _date.fromisoformat(str(raw_date)[:10])
            except ValueError:
                return Response({'detail': 'Expense date is not valid (use YYYY-MM-DD).'},
                                status=http.HTTP_400_BAD_REQUEST)
        else:
            expense_date = timezone.localdate()
        approver = User.objects.filter(pk=d.get('approver_id'), is_active=True).first()
        if not approver or not _is_approver(approver):
            return Response({'detail': 'Pick a senior accountant to process this.'}, status=http.HTTP_400_BAD_REQUEST)
        if approver.id == request.user.id:
            return Response({'detail': 'You cannot send your own refund to yourself — pick another accountant.'},
                            status=http.HTTP_400_BAD_REQUEST)
        invoices = request.FILES.getlist('invoices')
        if not invoices:
            return Response({'detail': 'Attach at least one invoice.'}, status=http.HTTP_400_BAD_REQUEST)
        bad = _check_uploads(invoices, max_files=12)
        if bad:
            return Response({'detail': bad}, status=http.HTTP_400_BAD_REQUEST)

        claim = ExpenseClaim.objects.create(
            profile=prof, expense_date=expense_date,
            category=(d.get('category') or '')[:80], amount=amount,
            currency=(d.get('currency') or 'BWP')[:3], description=description,
            approver=approver, status=ExpenseClaim.Status.SUBMITTED,
            submitted_at=timezone.now())
        for f in invoices:
            ExpenseClaimAttachment.objects.create(claim=claim, file=f, uploaded_by=request.user)
        _email_link(approver.email, 'Omni — a refund needs your review',
                    'A colleague has submitted a refund for you to process.', claim)
        return Response(_claim_dict(claim), status=http.HTTP_201_CREATED)


class MyExpenseClaimsView(APIView):
    """Requester dashboard — my refunds + their live status."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (ExpenseClaim.objects.filter(profile__user=request.user)
              .select_related('profile__user', 'approver').prefetch_related('invoices')
              .order_by('-created_at'))
        return Response([_claim_dict(c) for c in qs])


class ExpenseApproverQueueView(APIView):
    """Senior accountant's queue — refunds routed to me, awaiting my processing."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_approver(request.user):
            return Response({'detail': 'Senior accountants only.'}, status=http.HTTP_403_FORBIDDEN)
        # MY queue — always, superuser included (CFO 2026-08-07). The
        # superuser bypass turned a card headed "Refunds for YOU to process"
        # into the whole company's list: the CFO was shown refunds routed to a
        # different accountant, told to pay them in FNB, and reasonably read
        # that as "already approved, so why am I seeing it". A
        # personal queue must be personal. The full company list lives on the
        # desktop Refunds screen, which is the right place for oversight.
        qs = (ExpenseClaim.objects
              .filter(status=ExpenseClaim.Status.SUBMITTED, approver=request.user))
        qs = qs.select_related('profile__user', 'approver').prefetch_related('invoices').order_by('submitted_at')
        return Response([_claim_dict(c) for c in qs])


class ExpenseProcessView(APIView):
    """Accountant loads the payment (FNB, manual) + uploads proof -> goes to the CFO."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        c = ExpenseClaim.objects.filter(pk=pk).select_related('profile__user').first()
        if not c:
            return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
        if not _is_approver(request.user) or (c.approver_id and c.approver_id != request.user.id
                                              and not request.user.is_superuser):
            return Response({'detail': 'Only the assigned senior accountant can process this.'},
                            status=http.HTTP_403_FORBIDDEN)
        if c.status != ExpenseClaim.Status.SUBMITTED:
            return Response({'detail': f'This refund is already {c.get_status_display()}.'},
                            status=http.HTTP_400_BAD_REQUEST)
        proof = request.FILES.get('payment_proof')
        if not proof:
            return Response({'detail': 'Upload the payment proof (FNB screenshot).'},
                            status=http.HTTP_400_BAD_REQUEST)
        bad = _check_uploads([proof], max_files=1)
        if bad:
            return Response({'detail': bad}, status=http.HTTP_400_BAD_REQUEST)
        c.payment_proof = proof
        c.processed_by = request.user
        c.processed_at = timezone.now()
        c.status = ExpenseClaim.Status.PENDING_CFO
        fields = ['payment_proof', 'processed_by', 'processed_at', 'status', 'updated_at']
        # Link the GL account so it's easy to post after payment (optional tag; no JE).
        acct_id = request.data.get('gl_account_id')
        if acct_id:
            acct = _expense_accounts().filter(pk=acct_id).first()
            if not acct:
                return Response({'detail': 'The GL account must be an expense line '
                                           '(not a balance-sheet, revenue, or reinsurance account).'},
                                status=http.HTTP_400_BAD_REQUEST)
            c.gl_account = acct
            fields.append('gl_account')
        c.save(update_fields=fields)
        _email_link('pganesharajah@alphadirect.co.bw', 'Omni — a refund is ready for your approval',
                    'A refund has been processed and needs your approval.', c)
        return Response(_claim_dict(c))


class ExpenseRejectView(APIView):
    """Accountant OR CFO rejects -> returns to the requester with a reason."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        c = ExpenseClaim.objects.filter(pk=pk).select_related('profile__user').first()
        if not c:
            return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
        may = (request.user.is_superuser
               or (c.approver_id == request.user.id and c.status == ExpenseClaim.Status.SUBMITTED)
               or (_is_cfo(request.user) and c.status == ExpenseClaim.Status.PENDING_CFO))
        if not may:
            return Response({'detail': 'You cannot reject this refund at its current stage.'},
                            status=http.HTTP_403_FORBIDDEN)
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'detail': 'Give a reason so the requester can fix it.'},
                            status=http.HTTP_400_BAD_REQUEST)
        c.status = ExpenseClaim.Status.REJECTED
        c.reject_reason = reason
        c.save(update_fields=['status', 'reject_reason', 'updated_at'])
        _email_link(c.profile.user.email, 'Omni — your refund needs changes',
                    f'Your refund was sent back: {reason}', c)
        return Response(_claim_dict(c))


class ExpenseMarkPaidView(APIView):
    """Close out a refund that HAS been paid, without pretending it was rejected.

    Requested by Bharath Balasubramanian 2026-08-17: "They are rejecting the
    reimbursement instead of approving this once paid. If you can keep an option
    of paid reimbursement - that will keep a proper track of these. Tomorrow,
    someone can scam us by saying we haven't paid."

    He is right. `Status.PAID` existed but nothing ever set it, so a refund paid
    outside the accountant + CFO route had nowhere to go and was closed with
    Reject + reason "PAID". That records "Returned to requester" against money
    that left the bank, and emails the requester that their refund needs changes.

    Who may do it: the assigned senior accountant, the CFO, or a superuser — the
    same people who can already move the refund. A reference is REQUIRED: an
    unevidenced "trust me, it was paid" is the very gap this closes.
    """
    permission_classes = [IsAuthenticated]

    # Anything not already finished can be closed as paid. REJECTED is included
    # deliberately: it is how the mis-recorded ones got there, so this is also
    # the route to correct them.
    CLOSEABLE = (
        ExpenseClaim.Status.SUBMITTED,
        ExpenseClaim.Status.PENDING_CFO,
        ExpenseClaim.Status.APPROVED,
        ExpenseClaim.Status.REJECTED,
    )

    def post(self, request, pk):
        c = ExpenseClaim.objects.filter(pk=pk).select_related('profile__user').first()
        if not c:
            return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)

        may = (request.user.is_superuser
               or _is_cfo(request.user)
               or (_is_approver(request.user)
                   and (not c.approver_id or c.approver_id == request.user.id)))
        if not may:
            return Response(
                {'detail': 'Only the senior accountant handling this refund, or '
                           'the CFO, can mark it paid.'},
                status=http.HTTP_403_FORBIDDEN)

        if c.status == ExpenseClaim.Status.PAID:
            return Response({'detail': 'This refund is already marked paid.'},
                            status=http.HTTP_400_BAD_REQUEST)
        if c.status not in self.CLOSEABLE:
            return Response(
                {'detail': f'A refund still in "{c.get_status_display()}" cannot be '
                           f'marked paid — submit it first.'},
                status=http.HTTP_400_BAD_REQUEST)

        reference = (request.data.get('reference') or '').strip()
        if not reference:
            return Response(
                {'detail': 'Give the payment reference (the FNB reference, or the '
                           'payroll period it went out on). Marking money paid '
                           'without evidence is the problem this fixes.'},
                status=http.HTTP_400_BAD_REQUEST)

        was = c.status
        c.status         = ExpenseClaim.Status.PAID
        c.paid_by        = request.user
        c.paid_at        = timezone.now()
        c.paid_reference = reference[:120]
        c.paid_note      = (request.data.get('note') or '').strip()
        fields = ['status', 'paid_by', 'paid_at', 'paid_reference', 'paid_note',
                  'updated_at']
        # Correcting a wrongly-rejected refund: clear the rejection so the record
        # no longer reads "Returned to requester" for money that was paid.
        if was == ExpenseClaim.Status.REJECTED and c.reject_reason:
            c.paid_note = (
                (c.paid_note + ' ' if c.paid_note else '')
                + f'[corrected: was rejected with reason "{c.reject_reason}"]').strip()
            c.reject_reason = ''
            fields.append('reject_reason')
        c.save(update_fields=fields)

        _email_link(c.profile.user.email, 'Omni — your refund is marked paid',
                    f'Your refund has been recorded as PAID (reference {reference}).', c)
        return Response(_claim_dict(c))


class ExpenseCfoQueueView(APIView):
    """CFO queue — refunds processed + awaiting CFO approval (his 'pending' list)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_cfo(request.user):
            return Response({'detail': 'CFO only.'}, status=http.HTTP_403_FORBIDDEN)
        qs = (ExpenseClaim.objects.filter(status=ExpenseClaim.Status.PENDING_CFO)
              .select_related('profile__user', 'approver').prefetch_related('invoices')
              .order_by('processed_at'))
        return Response([_claim_dict(c) for c in qs])


class ExpenseApproveView(APIView):
    """CFO approves — payment already loaded in FNB by the accountant."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not _is_cfo(request.user):
            return Response({'detail': 'CFO only.'}, status=http.HTTP_403_FORBIDDEN)
        c = ExpenseClaim.objects.filter(pk=pk).select_related('profile__user').first()
        if not c:
            return Response({'detail': 'Not found.'}, status=http.HTTP_404_NOT_FOUND)
        if c.status != ExpenseClaim.Status.PENDING_CFO:
            return Response({'detail': f'This refund is {c.get_status_display()}, not awaiting you.'},
                            status=http.HTTP_400_BAD_REQUEST)
        c.status = ExpenseClaim.Status.APPROVED
        c.approved_by = request.user
        c.approved_at = timezone.now()
        c.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        _email_link(c.profile.user.email, 'Omni — your refund is approved',
                    'Your refund has been approved by the CFO.', c)
        return Response(_claim_dict(c))


class ExpenseGlAccountsView(APIView):
    """EXPENSE GL accounts the accountant may link (no balance-sheet / revenue /
    reinsurance). Searchable picker for the Process modal."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_approver(request.user):
            return Response({'detail': 'Senior accountants only.'}, status=http.HTTP_403_FORBIDDEN)
        qs = _expense_accounts(request.query_params.get('search', ''),
                               request.query_params.get('company', ''))[:50]
        return Response([{'id': str(a.id), 'code': a.code, 'name': a.name} for a in qs])


class ExpenseSuggestGlView(APIView):
    """DeepSeek suggests the EXPENSE line from the refund description. The model is
    only ever shown expense accounts, so it can only suggest expense lines."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        if not _is_approver(request.user):
            return Response({'detail': 'Senior accountants only.'}, status=http.HTTP_403_FORBIDDEN)
        desc = (request.data.get('description') or '').strip()
        company = request.data.get('company', '')
        if pk and not desc:
            c = ExpenseClaim.objects.filter(pk=pk).first()
            if c:
                desc = f'{c.category} — {c.description}'.strip(' —')
        if not desc:
            return Response({'suggestions': []})
        exp = list(_expense_accounts('', company).values('code', 'name', 'account_type', 'sub_type'))
        from core.ai_assist import suggest_je_accounts
        raw = suggest_je_accounts(desc, exp)                    # [{code, reason}], expense-only input
        allowed = {a['code']: a['name'] for a in exp}
        out = [{'id': None, 'code': s['code'], 'name': allowed[s['code']], 'reason': s.get('reason', '')}
               for s in raw if s.get('code') in allowed]
        # resolve ids so the FE can select directly
        codes = [o['code'] for o in out]
        ids = {a.code: str(a.id) for a in _expense_accounts('', company).filter(code__in=codes)}
        for o in out:
            o['id'] = ids.get(o['code'])
        return Response({'suggestions': out})


class ExpenseFileView(APIView):
    """Authenticated download of a claim's files. Production does NOT serve raw
    /media/ URLs (DEBUG=False), so invoices + the FNB payment proof are fetched
    through this endpoint instead — visible only to the requester, the assigned
    accountant, the approver pool, or the CFO (Fable review fix, 2026-07-13).

      GET /expense-claims/<pk>/file/?kind=proof
      GET /expense-claims/<pk>/file/?kind=invoice&i=0
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from django.http import FileResponse, Http404
        c = (ExpenseClaim.objects.select_related('profile__user')
             .prefetch_related('invoices').filter(pk=pk).first())
        if not c:
            raise Http404
        allowed = (request.user.id == c.profile.user_id
                   or request.user.id == c.approver_id
                   or _is_approver(request.user) or _is_cfo(request.user))
        if not allowed:
            return Response({'detail': 'Not your claim.'}, status=http.HTTP_403_FORBIDDEN)
        kind = (request.query_params.get('kind') or 'proof').strip()
        if kind == 'proof':
            f = c.payment_proof
        else:
            try:
                idx = int(request.query_params.get('i', '0'))
            except ValueError:
                idx = 0
            files = [i.file for i in c.invoices.all() if i.file]
            f = files[idx] if 0 <= idx < len(files) else None
        if not f:
            raise Http404
        return FileResponse(f.open('rb'), as_attachment=True,
                            filename=(f.name or 'file').split('/')[-1])
