"""petty_cash/api_views.py — DRF endpoints for petty cash."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import CompanyScopedViewSetMixin, UNRESOLVED, _resolve_company_id
from core.models import allowed_company_ids
from ledger.models import FiscalPeriod

from . import services
from .models import (
    PettyCashLocation,
    PettyCashReimbursement,
    PettyCashVoucher,
    PettyCashVoucherReceipt,
)
from .serializers import (
    CreateReimbursementSerializer,
    PettyCashLocationSerializer,
    PettyCashReimbursementDetailSerializer,
    PettyCashReimbursementListSerializer,
    PettyCashVoucherCreateSerializer,
    PettyCashVoucherListSerializer,
    PettyCashVoucherReceiptSerializer,
    PreviewReimbursementSerializer,
    RejectVoucherSerializer,
)


# Receipts: images + PDFs only, 15 MB cap — a phone photo or a scanned slip.
_RECEIPT_MAX_BYTES = 15 * 1024 * 1024
_RECEIPT_TYPES = {
    'image/jpeg', 'image/png', 'image/heic', 'image/heif', 'image/webp',
    'application/pdf',
}


def _err(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': detail}, status=code)


def _validation_to_response(exc):
    """Translate a Django ValidationError to a DRF Response."""
    if hasattr(exc, 'message_dict'):
        return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
    if hasattr(exc, 'messages'):
        return Response({'detail': exc.messages}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# Alpha Direct group domains — a shared petty-cash link may only be emailed to
# a group address, never leaked to an outside inbox (gmail etc.).
_LINK_EMAIL_DOMAINS = {
    'alphadirect.co.bw', 'alphadirect.co.zm', 'alphadirect.co.za',
    'insurance.co.bw', 'theriskco.com', 'quantum.co.bw', 'motorliquidators.co.bw',
}


def _valid_company_email(email):
    import re
    email = (email or '').strip()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return False, 'Enter a valid email address.'
    if email.rsplit('@', 1)[1].lower() not in _LINK_EMAIL_DOMAINS:
        return False, 'The link can only be emailed to an Alpha Direct group address.'
    return True, ''


def _send_petty_cash_link(*, kind, number, path, recipient, note, sender, summary=''):
    """Email a deep link to a petty-cash record to a chosen group recipient.

    The link goes to whoever the sender picks (self-service "I can't find the
    request — send it to the right person"). Sent from the house omni mailbox
    (not send-as the user) with the sender named in the body; CFO is CC'd.
    """
    from core.notifications import send_with_cfo_cc, _link
    url = _link(path)
    who = (getattr(sender, 'get_full_name', lambda: '')() or getattr(sender, 'username', 'A colleague'))
    lines = [
        f'{who} has sent you a {kind} to action.',
        '',
        f'{kind.capitalize()}: {number}',
    ]
    if summary:
        lines.append(summary)
    lines += ['', f'Open it here: {url}']
    if note:
        lines += ['', f'Note from {who}: {note}']
    lines += ['', 'Regards,', 'Alpha Direct omni']
    send_with_cfo_cc(f'{kind.capitalize()} {number} — for your action',
                     '\n'.join(lines), [recipient])


def _scoped_locations(request):
    """PettyCashLocation queryset limited to the caller's allowed companies.

    The workflow actions (submit/approve/…) are protected because they go
    through the mixin's scoped get_object(); but voucher-create, reimbursement-
    create and preview resolve the location themselves, so they MUST use this
    (not the raw manager) or a user of company A can create/preview against
    company B's tin. Mirrors CompanyScopedViewSetMixin's strict rules exactly:
    honour an explicit ?company=, else clamp to the user's allowed set, and
    deny a no-grant / unresolved caller.
    """
    field = 'company_id'
    qs = PettyCashLocation.objects.all()
    allowed = allowed_company_ids(request.user)
    cid = _resolve_company_id(request)
    if cid is UNRESOLVED:
        return qs.none()
    if cid is not None:
        if allowed != {'*'} and str(cid) not in allowed:
            return qs.none()
        return qs.filter(**{field: cid})
    if allowed == {'*'}:
        return qs
    if not allowed:
        return qs.none()
    return qs.filter(**{f'{field}__in': list(allowed)})


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class PettyCashLocationViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    # CFO directive 2026-05-19 (Manus master guide § 1): scope per company.
    # The location now carries its own `company` FK (the petty-cash GL account
    # is a shared/global account, so owner_company was always NULL and the old
    # walk left every location unscoped) — scope on it directly.
    company_lookup_field = 'company_id'
    queryset = PettyCashLocation.objects.select_related(
        'custodian', 'petty_cash_account', 'reimbursing_bank_account',
    )
    serializer_class = PettyCashLocationSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(audit_user=self.request.user)


# ---------------------------------------------------------------------------
# Voucher
# ---------------------------------------------------------------------------

class PettyCashVoucherViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    company_lookup_field = 'location__company_id'
    queryset = PettyCashVoucher.objects.select_related(
        'location', 'expense_account', 'created_by',
        'submitted_by', 'approved_by', 'journal_entry', 'reimbursement',
    )
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return PettyCashVoucherCreateSerializer
        return PettyCashVoucherListSerializer

    def _voucher_data(self, voucher):
        # Serialise WITH request context so viewer_can_code resolves — otherwise
        # every post-action response would report can_code=False and the coder's
        # buttons would vanish after their first click.
        return PettyCashVoucherListSerializer(
            voucher, context={'request': self.request}).data

    def get_queryset(self):
        # super() so CompanyScopedViewSetMixin's company filter + allowed-
        # companies gate apply; `self.queryset` directly would bypass both.
        qs = super().get_queryset()
        loc = self.request.query_params.get('location')
        if loc:
            qs = qs.filter(location_id=loc)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    # A4 (CFO board item, 15-Sep-2026) — the Welcome page must NEVER read the
    # company-wide register to show "my petty cash". That list is scoped to the
    # company, not to the person, so rendering it on a personal card would show
    # one employee another employee's voucher. This action is the requester-only
    # read: created_by = the caller, on top of the company scope super() applies.
    @action(detail=False, methods=['get'], url_path='mine')
    def mine(self, request):
        qs = self.get_queryset().filter(created_by=request.user).order_by('-created_at')
        return Response(PettyCashVoucherListSerializer(
            qs, many=True, context={'request': request}).data)

    def create(self, request, *args, **kwargs):
        from core.models import UserProfile, get_user_profile, user_is_approver_only
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        expense_account = ser.validated_data.get('expense_account')
        # CFO directive 2026-08-10 — the person who RAISES a voucher (e.g. an EA
        # requesting cash for a staff-welfare cake) no longer codes the GL; the
        # petty-cash finance team sets the expense account when they post it. So
        # a GL-LESS voucher (a plain request) may be raised by any staff member.
        # The maker gate below applies ONLY when a GL account is supplied — a
        # coded voucher is still a finance-maker action, and posting always stays
        # a two-signature finance job (approve_voucher, unchanged). This keeps
        # segregation of duties intact: no non-finance user can inject a coded
        # entry, and no requester can post their own cash.
        if expense_account is not None and not getattr(request.user, 'is_superuser', False):
            # CFO directive 2026-07-05 — an approver-only role (Finance Manager)
            # approves petty cash; it does not enter coded vouchers.
            if user_is_approver_only(request.user):
                return Response(
                    {'detail': 'Petty cash vouchers are entered by an Accountant / Senior '
                               'Accountant / Financial Controller. A Finance Manager approves '
                               'vouchers, but does not enter them.'},
                    status=status.HTTP_403_FORBIDDEN)
            # Positive maker gate: only a finance MAKER title may originate a
            # coded voucher. CREATION_TITLES is the canonical JE-creator set.
            prof = get_user_profile(request.user)
            if prof is None or prof.title not in UserProfile.CREATION_TITLES:
                return Response(
                    {'detail': 'You are not authorised to enter a coded petty cash voucher. '
                               'Coding is limited to Accountant / Senior Accountant / '
                               'Financial Controller / CFO. Raise the request without a GL '
                               'account and the petty-cash team will code it.'},
                    status=status.HTTP_403_FORBIDDEN)
        # Tenant guard: the location FK on the create-serializer is unscoped,
        # so confirm the caller may act on this location's company before we
        # plant a voucher in another entity's tin.
        loc = ser.validated_data['location']
        if not _scoped_locations(request).filter(pk=loc.pk).exists():
            return Response(
                {'detail': 'Location not found.'},
                status=status.HTTP_404_NOT_FOUND)
        # Per-entity input override (CFO directive 2026-08-31): a ring-fenced tin
        # (Unicoin) may only be raised by its named people. Ordinary tins keep
        # the open rule — any staff may raise, the coded-voucher maker gate above
        # still applies.
        if not services.can_input_for_location(request.user, loc):
            return Response(
                {'detail': 'You are not authorised to raise a petty cash voucher '
                           'for this location. It is restricted to named staff.'},
                status=status.HTTP_403_FORBIDDEN)
        try:
            voucher = services.create_voucher(
                location=ser.validated_data['location'],
                voucher_date=ser.validated_data.get('voucher_date'),
                payee=ser.validated_data['payee'],
                amount=ser.validated_data['amount'],
                expense_account=expense_account,
                description=ser.validated_data['description'],
                receipt_reference=ser.validated_data.get('receipt_reference', ''),
                receipt_attached=ser.validated_data.get('receipt_attached', False),
                user=request.user,
            )
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(
            self._voucher_data(voucher),
            status=status.HTTP_201_CREATED,
        )

    def _guard_edit_location(self, request, instance):
        """A DRAFT edit can rewrite `location` (the create-serializer allows it),
        so the tenant-scope + named-inputter gate that create() applies must be
        re-applied on every edit path too. Otherwise a draft raised on an open
        tin could be PATCHed onto a ring-fenced tin (Unicoin) and submitted,
        defeating the "only named staff may raise" rule. Returns an error
        Response to abort, or None to proceed. (Fable 5, checklist L20, 2026-08-31.)"""
        loc_id = request.data.get('location')
        if loc_id:
            target = PettyCashLocation.objects.filter(pk=loc_id).first()
            if target is None or not _scoped_locations(request).filter(pk=target.pk).exists():
                return Response({'detail': 'Location not found.'},
                                status=status.HTTP_404_NOT_FOUND)
        else:
            target = instance.location
        if not services.can_input_for_location(request.user, target):
            return Response(
                {'detail': 'You are not authorised to raise a petty cash voucher '
                           'for this location. It is restricted to named staff.'},
                status=status.HTTP_403_FORBIDDEN)
        return None

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != PettyCashVoucher.Status.DRAFT:
            return _err('Only draft vouchers can be edited.')
        guard = self._guard_edit_location(request, instance)
        if guard is not None:
            return guard
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != PettyCashVoucher.Status.DRAFT:
            return _err('Only draft vouchers can be edited.')
        guard = self._guard_edit_location(request, instance)
        if guard is not None:
            return guard
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != PettyCashVoucher.Status.DRAFT:
            return _err('Only draft vouchers can be deleted.')
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        voucher = self.get_object()
        try:
            services.submit_voucher(voucher, request.user)
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(self._voucher_data(voucher))

    @action(detail=True, methods=['post'])
    def amend(self, request, pk=None):
        # Keetile asked for this and the CFO approved it on 5 Aug 2026: the custodian should be
        # able to correct the amount or the GL line instead of bouncing the voucher back for a
        # typo. A reason is required, the requester's original figure is kept, and any signature
        # already given is cleared — signing means agreeing to a FIGURE.
        voucher = self.get_object()
        account = None
        acc_id = request.data.get('expense_account') or request.data.get('expense_account_id')
        if acc_id:
            from ledger.models import Account
            account = Account.objects.filter(pk=acc_id).first()
            if account is None:
                return _err('That GL account does not exist.')
        amount = request.data.get('amount')
        if amount not in (None, ''):
            # A typed amount arrives as text. Decimal() on 'abc' raises InvalidOperation, which
            # is not a ValidationError — it became a 500 instead of "that is not a number".
            from decimal import Decimal, InvalidOperation
            try:
                amount = Decimal(str(amount).replace(',', '').strip())
            except (InvalidOperation, ValueError):
                return _err('That amount is not a number.')
        # Same button, two paths. Before signing, an amendment just rewrites the voucher and
        # clears the signatures. After posting — the window the CFO opened on 7 Aug 2026 — the
        # GL is already carrying the wrong figure, so the correction has to reverse that entry
        # and post a fresh one. It closes for good once the replenishment pays the voucher out.
        amend_fn = (
            services.amend_posted_voucher
            if voucher.status == PettyCashVoucher.Status.POSTED
            else services.amend_voucher
        )
        try:
            voucher = amend_fn(
                voucher, request.user,
                amount=amount if amount not in (None, '') else None,
                expense_account=account,
                reason=(request.data.get('reason') or ''))
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(self._voucher_data(voucher))

    @action(detail=True, methods=['post'], url_path='return-to-draft')
    def return_to_draft(self, request, pk=None):
        # Keetile's second ask, CFO approved 7 Aug 2026: a posted voucher that is wrong in
        # more than its amount — wrong payee, wrong date, or should never have been paid —
        # comes back to draft for a full edit. The JE is reversed, not rewritten, and the
        # signatures are cleared so it must be signed again. Refused once it has been paid.
        voucher = self.get_object()
        try:
            voucher = services.unpost_voucher(
                voucher, request.user, reason=(request.data.get('reason') or ''))
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(self._voucher_data(voucher))

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        voucher = self.get_object()
        try:
            services.approve_voucher(voucher, request.user)
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(self._voucher_data(voucher))

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        voucher = self.get_object()
        ser = RejectVoucherSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            services.reject_voucher(voucher, request.user, ser.validated_data['reason'])
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(self._voucher_data(voucher))

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        voucher = self.get_object()
        try:
            services.reopen_voucher(voucher, request.user)
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(self._voucher_data(voucher))

    @action(detail=True, methods=['get', 'post'], url_path='receipts',
            parser_classes=[MultiPartParser, FormParser])
    def receipts(self, request, pk=None):
        """GET  list this voucher's receipts.
        POST upload a receipt (multipart: file). Photo/PDF, 15 MB max.
        Receipts can be added at any stage (evidence), but not once the
        voucher is reimbursed (locked)."""
        voucher = self.get_object()   # company-scoped
        if request.method == 'GET':
            qs = voucher.receipts.select_related('uploaded_by').all()
            return Response(PettyCashVoucherReceiptSerializer(
                qs, many=True, context={'request': request}).data)

        if voucher.status == PettyCashVoucher.Status.REIMBURSED:
            return _err('This voucher is reimbursed and locked; receipts can no longer be changed.')
        upload = request.FILES.get('file')
        if upload is None:
            return _err('file is required')
        if (upload.size or 0) > _RECEIPT_MAX_BYTES:
            return _err('Receipt is too large (max 15 MB). Take a smaller photo or scan.')
        ctype = (getattr(upload, 'content_type', '') or '').lower()
        if ctype and ctype not in _RECEIPT_TYPES:
            return _err('Please upload a photo (JPG/PNG/HEIC) or a PDF.')
        rec = PettyCashVoucherReceipt(
            voucher=voucher, file=upload, filename=upload.name[:255],
            file_size_bytes=upload.size or 0, content_type=ctype,
            uploaded_by=request.user,
        )
        rec.save(audit_user=request.user,
                 audit_description=f'Attached receipt {upload.name} to {voucher.voucher_number}')
        return Response(
            PettyCashVoucherReceiptSerializer(rec, context={'request': request}).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'],
            url_path=r'receipts/(?P<receipt_id>[0-9a-f-]{36})/download')
    def download_receipt(self, request, pk=None, receipt_id=None):
        """Serve a receipt through the API, authenticated and company-scoped.

        Keetile, 5 Aug 2026: "when i try to download the attached receipt, i get a server
        error." The serializer had been handing out the raw MEDIA path (`file.url` →
        /media/...), and /media/ is not served in production — Django does not serve it and the
        proxy has no route for it, so every receipt download returned 404.

        Serving it here fixes that and closes a hole at the same time: a /media/ URL, had anyone
        ever pointed a web server at that directory, would have made every till slip and invoice
        readable by anyone with the link. This route goes through `get_object()`, so the same
        company scoping and permissions that guard the voucher guard its evidence.
        """
        voucher = self.get_object()          # company-scoped + permission-checked
        try:
            rec = voucher.receipts.get(pk=receipt_id)
        except PettyCashVoucherReceipt.DoesNotExist:
            return _err('Receipt not found.', code=status.HTTP_404_NOT_FOUND)
        if not rec.file:
            return _err('That receipt has no file stored against it.',
                        code=status.HTTP_404_NOT_FOUND)
        try:
            fh = rec.file.open('rb')
        except (FileNotFoundError, OSError):
            # The row exists but the file is gone — say so plainly instead of a 500.
            return _err('The stored file for this receipt is missing. Please attach it again.',
                        code=status.HTTP_404_NOT_FOUND)
        return FileResponse(
            fh, as_attachment=True,
            # The original filename, apostrophes and all — FileResponse handles the encoding.
            filename=rec.filename or (rec.file.name or 'receipt').split('/')[-1],
            content_type=rec.content_type or None)

    @action(detail=True, methods=['delete'],
            url_path=r'receipts/(?P<receipt_id>[0-9a-f-]{36})')
    def delete_receipt(self, request, pk=None, receipt_id=None):
        voucher = self.get_object()
        if voucher.status in (PettyCashVoucher.Status.POSTED, PettyCashVoucher.Status.REIMBURSED):
            return _err('Receipts on a posted voucher are locked as evidence and cannot be removed.')
        try:
            rec = voucher.receipts.get(pk=receipt_id)
        except PettyCashVoucherReceipt.DoesNotExist:
            return _err('Receipt not found.', code=status.HTTP_404_NOT_FOUND)
        rec.file.delete(save=False)
        rec.delete(audit_user=request.user, audit_description=f'Removed receipt {rec.filename}')
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def email_link(self, request, pk=None):
        """Email this voucher's link to a chosen group recipient (sender picks)."""
        voucher = self.get_object()   # company-scoped
        recipient = (request.data.get('recipient') or request.data.get('email') or '').strip()
        ok, err = _valid_company_email(recipient)
        if not ok:
            return _err(err)
        _send_petty_cash_link(
            kind='petty cash voucher',
            number=voucher.voucher_number,
            path=f'/petty-cash/{voucher.pk}',
            recipient=recipient,
            note=(request.data.get('note') or '').strip()[:500],
            sender=request.user,
            summary=f'{voucher.payee} — P{voucher.amount}',
        )
        return Response({'sent': True, 'recipient': recipient})

    @action(detail=False, methods=['post'])
    def suggest_account(self, request):
        """Aria suggests up to 3 expense GL accounts for what the cash was for.

        Advisory only: the custodian types the description/payee, Aria proposes
        accounts, the human picks. The server still enforces expense-only at
        create + approve, so a wrong or hallucinated suggestion cannot post.
        Reuses core.ai_assist.suggest_je_accounts, which runs the is_safe_for_ai
        PII scrub before any external call and returns [] if the text is unsafe
        or every reasoning engine is unreachable — never blocks voucher entry.
        """
        from core.ai_assist import suggest_je_accounts
        from ledger.models import Account

        description = (request.data.get('description') or '').strip()
        payee = (request.data.get('payee') or '').strip()
        if not description and not payee:
            return Response({'suggestions': []})
        text = f'{description} (payee: {payee})' if payee else description

        # Only real, active, non-summary expense accounts are candidates — so a
        # code the model invents outside this set is dropped, not returned.
        accounts = list(
            Account.objects.filter(account_type='expense', is_active=True)
            .exclude(is_summary_only=True)
            .values('code', 'name', 'account_type', 'sub_type')[:300]
        )
        by_code = {a['code']: a['name'] for a in accounts}
        suggestions = suggest_je_accounts(text, accounts)
        out = [
            {'code': s['code'], 'name': by_code[s['code']],
             'reason': s.get('reason', '')}
            for s in suggestions if s.get('code') in by_code
        ]
        return Response({'suggestions': out})


# ---------------------------------------------------------------------------
# Reimbursement
# ---------------------------------------------------------------------------

class PettyCashReimbursementViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    company_lookup_field = 'location__company_id'
    queryset = PettyCashReimbursement.objects.select_related(
        'location', 'period', 'created_by', 'posted_by', 'journal_entry',
    ).prefetch_related('vouchers')
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PettyCashReimbursementDetailSerializer
        return PettyCashReimbursementListSerializer

    def get_queryset(self):
        # super() so CompanyScopedViewSetMixin's company filter + allowed-
        # companies gate apply; `self.queryset` directly would bypass both.
        qs = super().get_queryset()
        loc = self.request.query_params.get('location')
        if loc:
            qs = qs.filter(location_id=loc)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def create(self, request, *args, **kwargs):
        ser = CreateReimbursementSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        location = get_object_or_404(
            _scoped_locations(request), pk=ser.validated_data['location'],
        )
        period = None
        if ser.validated_data.get('period'):
            period = get_object_or_404(
                FiscalPeriod, pk=ser.validated_data['period'],
            )
        try:
            reimb = services.create_reimbursement(
                location=location,
                period_start=ser.validated_data['period_start'],
                period_end=ser.validated_data['period_end'],
                user=request.user,
                period=period,
                notes=ser.validated_data.get('notes', ''),
            )
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(
            PettyCashReimbursementDetailSerializer(reimb).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != PettyCashReimbursement.Status.DRAFT:
            return _err('Only draft reimbursements can be deleted.')
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """Maker sends the top-up for FM review (DRAFT/REJECTED -> PENDING_FM)."""
        reimb = self.get_object()
        try:
            services.submit_reimbursement(reimb, request.user)
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(PettyCashReimbursementDetailSerializer(reimb).data)

    @action(detail=True, methods=['post'])
    def fm_review(self, request, pk=None):
        """FM approves the review and passes it to the CFO (PENDING_FM -> PENDING_CFO)."""
        reimb = self.get_object()
        try:
            services.fm_review_reimbursement(reimb, request.user)
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(PettyCashReimbursementDetailSerializer(reimb).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        reimb = self.get_object()
        ser = RejectVoucherSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            services.reject_reimbursement(reimb, request.user, ser.validated_data['reason'])
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(PettyCashReimbursementDetailSerializer(reimb).data)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        reimb = self.get_object()
        try:
            services.reopen_reimbursement(reimb, request.user)
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(PettyCashReimbursementDetailSerializer(reimb).data)

    @action(detail=True, methods=['post'])
    def email_link(self, request, pk=None):
        """Email this reimbursement's review link to a chosen group recipient —
        e.g. the maker sends it to Kago/Pako, or on to the CFO (sender picks)."""
        reimb = self.get_object()   # company-scoped
        recipient = (request.data.get('recipient') or request.data.get('email') or '').strip()
        ok, err = _valid_company_email(recipient)
        if not ok:
            return _err(err)
        _send_petty_cash_link(
            kind='petty cash reimbursement',
            number=reimb.reimbursement_number,
            path=f'/petty-cash/reimbursements/{reimb.pk}',
            recipient=recipient,
            note=(request.data.get('note') or '').strip()[:500],
            sender=request.user,
            summary=f'{reimb.location.name} — {reimb.get_status_display()}',
        )
        return Response({'sent': True, 'recipient': recipient})

    @action(detail=True, methods=['post'])
    def post_reimbursement(self, request, pk=None):
        """CFO's final 'approve in the bank' — posts the DR petty cash / CR bank
        JE (PENDING_CFO -> POSTED)."""
        reimb = self.get_object()
        try:
            services.post_reimbursement(reimb, request.user)
        except DjangoValidationError as exc:
            return _validation_to_response(exc)
        return Response(PettyCashReimbursementDetailSerializer(reimb).data)

    @action(detail=False, methods=['post'])
    def preview(self, request):
        ser = PreviewReimbursementSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        location = get_object_or_404(
            _scoped_locations(request), pk=ser.validated_data['location'],
        )
        result = services.preview_reimbursement(
            location,
            ser.validated_data['period_start'],
            ser.validated_data['period_end'],
        )
        return Response({
            'location_id': str(result['location'].pk),
            'location_name': result['location'].name,
            'period_start': str(result['period_start']),
            'period_end': str(result['period_end']),
            'voucher_count': result['voucher_count'],
            'total_amount': str(result['total_amount']),
            'cash_on_hand_before': str(result['cash_on_hand_before']),
            'cash_on_hand_after': str(result['cash_on_hand_after']),
            'float_amount': str(result['float_amount']),
            'vouchers': PettyCashVoucherListSerializer(
                result['vouchers'], many=True,
            ).data,
        })
