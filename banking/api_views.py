"""banking/api_views.py"""
import logging

from django.core.exceptions import ValidationError
from rest_framework import filters, mixins, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import CompanyScopedViewSetMixin
from core.permissions import CanViewFinancials
from .models import BankAccount, BankRecRule, BankStatement, BankStatementLine
from .rec_engine import match_statement_lines
from .serializers import (
    BankAccountSerializer,
    BankRecRuleSerializer,
    BankStatementDetailSerializer,
    BankStatementLineSerializer,
    BankStatementListSerializer,
)
from .services import BankStatementImporter, ReconciliationEngine

logger = logging.getLogger(__name__)


def _refresh_recon_state(stmt: BankStatement, user=None) -> None:
    """
    Recompute a statement's reconciliation status after any matching activity.

    Fully matched → PENDING_APPROVAL (not RECONCILED). Segregation of duties
    (BUG b72695a8): the person who matches the lines (reconciled_by) cannot
    finalise their own work — a second person holding `bank.approve` must
    approve it (see approve_reconciliation) before it counts as Reconciled and
    BankAccount.last_reconciled_date is stamped. This mirrors je.submit→approve
    and po.create→fm_approve.
    """
    unmatched = stmt.lines.filter(
        match_status=BankStatementLine.MatchStatus.UNMATCHED
    ).count()
    total = stmt.lines.count()
    if total and unmatched == 0:
        # All lines matched → hand off to an independent approver. Do NOT mark
        # RECONCILED or stamp last_reconciled_date here — that happens only on
        # approval by a different user.
        if stmt.status != BankStatement.Status.RECONCILED:
            stmt.status = BankStatement.Status.PENDING_APPROVAL
            if user is not None and stmt.reconciled_by_id is None:
                stmt.reconciled_by = user
            stmt.save(update_fields=['status', 'reconciled_by', 'updated_at'])
    elif unmatched < total:
        if stmt.status not in (BankStatement.Status.IN_PROGRESS,
                               BankStatement.Status.RECONCILED):
            stmt.status = BankStatement.Status.IN_PROGRESS
            stmt.save(update_fields=['status', 'updated_at'])



def _parse_query_date(raw, field_name):
    """A YYYY-MM-DD query parameter as a date, or None when absent.

    Raises DRF ValidationError (a 400) on anything unparseable rather than
    letting the raw string reach the ORM, where it raises Django's own
    ValidationError from inside the queryset and surfaces as a 500.
    """
    from django.utils.dateparse import parse_date
    from rest_framework.exceptions import ValidationError as DRFValidationError

    raw = (raw or '').strip()
    if not raw:
        return None
    # parse_date() returns None for a malformed string but RAISES ValueError
    # for a well-formed impossible one ("2026-13-01"), which was still a 500.
    # Found 15-Sep-2026 by the BANK-008 filter test; also protects the
    # transaction-lines filter below, which has always called this helper.
    try:
        parsed = parse_date(raw)
    except ValueError:
        parsed = None
    if parsed is None:
        raise DRFValidationError(
            {field_name: f'"{raw}" is not a date. Use YYYY-MM-DD, e.g. 2026-09-12.'})
    return parsed


def compute_match_confidence(
    statement_amount,
    candidate_amount,
    date_gap_days,
    reference_match,
):
    """
    Compute match confidence for a statement line → payment/JE pair.

    Pure function — no side effects, deterministic output.
    Scoring: 100 if amounts equal ∧ date gap ≤ 3 days ∧ reference match;
             90 if amounts equal ∧ date gap ≤ 7 days;
             70 if amounts equal otherwise.

    Args:
        statement_amount: Decimal from BankStatementLine.amount
        candidate_amount: Decimal from Payment.amount or JE equivalent
        date_gap_days: int, absolute days between transaction dates
        reference_match: bool, case-insensitive overlap of refs

    Returns:
        (confidence: int, explanation: dict) where explanation includes
        amount_equal, statement_amount, candidate_amount, date_gap_days,
        reference_match, and confidence.
    """
    from decimal import Decimal

    amount_equal = abs(Decimal(statement_amount)) == abs(Decimal(candidate_amount))

    if amount_equal and date_gap_days <= 3 and reference_match:
        confidence = 100
    elif amount_equal and date_gap_days <= 7:
        confidence = 90
    elif amount_equal:
        confidence = 70
    else:
        confidence = 0

    explanation = {
        'amount_equal': amount_equal,
        'statement_amount': str(statement_amount),
        'candidate_amount': str(candidate_amount),
        'date_gap_days': date_gap_days,
        'reference_match': reference_match,
        'confidence': confidence,
    }

    return confidence, explanation

class BankAccountViewSet(CompanyScopedViewSetMixin,
                          mixins.ListModelMixin,
                          mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    # CFO structural audit 2026-05-19: scope BankAccount via its GL account's
    # owner_company so the topbar entity filter survives.
    # SECURITY FIX (2026-07-14): this had no gate beyond IsAuthenticated — any
    # employee scoped to a company could read its bank account numbers.
    permission_classes = [IsAuthenticated, CanViewFinancials]
    company_lookup_field = 'gl_account__owner_company_id'
    queryset = BankAccount.objects.select_related(
        'gl_account', 'currency_code'
    ).filter(
        is_active=True,
        gl_account__is_active=True,           # honour BANK-003 deactivations
        gl_account__is_bank_account=True,     # exclude post-reclass non-cash GLs (BANK-005b)
    ).order_by('bank_name', 'account_name')
    serializer_class = BankAccountSerializer
    filter_backends  = [filters.SearchFilter]
    search_fields    = ['bank_name', 'account_name', 'account_number']

    def get_queryset(self):
        # Company filter is applied by CompanyScopedViewSetMixin via the
        # `company_lookup_field = 'gl_account__owner_company_id'` override.
        return super().get_queryset()

    def get_serializer_context(self):
        # CFO directive 2026-05-25 (BANK-001): the /banking page must
        # show the GL truth, not the cached `current_balance` field.
        # Pull JEL aggregate for every linked GL account in one query
        # + the latest BankStatement closing balance per BankAccount.
        from decimal import Decimal
        from django.db.models import Sum
        from ledger.models import JournalEntry, JournalEntryLine

        ctx = super().get_serializer_context()
        qs = self.get_queryset()
        gl_ids = list(qs.values_list('gl_account_id', flat=True))
        zero = Decimal('0.00')

        book_balances: dict = {}
        if gl_ids:
            rows = (
                JournalEntryLine.objects
                .filter(
                    account_id__in=gl_ids,
                    journal_entry__status=JournalEntry.Status.POSTED,
                )
                .values('account_id')
                .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
            )
            for r in rows:
                bal = (r['dr'] or zero) - (r['cr'] or zero)
                book_balances[r['account_id']] = str(bal.quantize(Decimal('0.01')))
        ctx['book_balances'] = book_balances

        # BANK-007 (Kelvin Kimani / Lefika, 2026-09-15): the page showed GL
        # as-of-today beside a statement closing balance at an OLDER date and
        # never said so, which reads as an error. Reuse the existing, correct
        # reconciliation (services.get_reconciliation_report cuts the GL at
        # entry_date__lte=statement_date) so the difference shown is
        # timing-correct rather than an apples-to-oranges subtraction.
        from .services import get_reconciliation_report

        latest_statements: dict = {}
        ba_ids = list(qs.values_list('id', flat=True))
        if ba_ids:
            for stmt in (
                BankStatement.objects.filter(bank_account_id__in=ba_ids)
                .select_related('bank_account__gl_account')
                .order_by('bank_account_id', '-statement_date')
            ):
                if stmt.bank_account_id not in latest_statements:
                    rec = get_reconciliation_report(stmt)
                    latest_statements[stmt.bank_account_id] = {
                        'closing_balance': stmt.closing_balance,
                        'statement_date': stmt.statement_date,
                        'statement_id': stmt.id,
                        'gl_at_statement_date': rec['gl_balance'],
                        'difference': rec['difference'],
                        'is_reconciled': rec['is_reconciled'],
                        'unmatched_lines': rec['lines']['unmatched'],
                    }
        ctx['latest_statements'] = latest_statements
        return ctx


class BankStatementViewSet(CompanyScopedViewSetMixin,
                            mixins.ListModelMixin,
                            mixins.RetrieveModelMixin,
                            viewsets.GenericViewSet):
    # CFO directive 2026-05-19 (Manus master guide § 1): scope statements
    # via the bank account's GL account owner_company.
    # SECURITY FIX (2026-07-14): this had no gate beyond IsAuthenticated — any
    # employee scoped to a company could read its bank statements.
    permission_classes = [IsAuthenticated, CanViewFinancials]
    company_lookup_field = 'bank_account__gl_account__owner_company_id'
    queryset = BankStatement.objects.select_related(
        'bank_account', 'imported_by'
    ).order_by('-statement_date', '-import_date')
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields   = ['statement_number', 'bank_account__account_name', 'file_name']
    ordering_fields = ['statement_date', 'import_date', 'status']

    def get_serializer_class(self):
        if self.action in ('retrieve', 'import_statement', 'run_matching'):
            return BankStatementDetailSerializer
        return BankStatementListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ba = self.request.query_params.get('bank_account')
        if ba:
            qs = qs.filter(bank_account_id=ba)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        # BANK-008 (Lefika, 2026-09-15): statements could not be viewed a month
        # at a time. Parsed through _parse_query_date above — a malformed date
        # must be a 400, not a 500 from inside the queryset (same reason the
        # lines endpoint uses it).
        from_date = _parse_query_date(
            self.request.query_params.get('from_date'), 'from_date')
        if from_date:
            qs = qs.filter(statement_date__gte=from_date)
        to_date = _parse_query_date(
            self.request.query_params.get('to_date'), 'to_date')
        if to_date:
            qs = qs.filter(statement_date__lte=to_date)
        return qs

    @action(
        detail=False, methods=['post'], url_path='import',
        parser_classes=[parsers.MultiPartParser, parsers.FormParser],
    )
    def import_statement(self, request):
        """
        POST /api/v1/bank-statements/import/
        Body (multipart/form-data):
          file            - CSV or .xlsx statement file
          bank_account_id - UUID of BankAccount
          format_name     - optional BankStatementFormat name. When omitted the
                            columns are read off the file's own header row.

        Bug 713d6218 (2026-08-03): this used to default to a format named
        'FNB BWP Current Account'. No BankStatementFormat rows existed on prod,
        so every single upload returned "Statement format ... not found" — the
        button had never worked. Excel files also died on .decode(). Now a named
        format still wins if given, otherwise the file describes itself.
        """
        from .models import BankStatementFormat
        from .statement_files import (
            StatementFileError, build_format_from_file, strip_preamble, to_csv_text,
        )

        csv_file       = request.FILES.get('file')
        # Accept either key — the upload modal historically posted 'bank_account'
        # while this view read 'bank_account_id', which surfaced as a spurious
        # "bank_account_id is required" (2026-06-08). Tolerate both.
        bank_account_id = request.data.get('bank_account_id') or request.data.get('bank_account')
        format_name    = (request.data.get('format_name') or '').strip()

        if not csv_file:
            return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)
        if not bank_account_id:
            return Response({'error': 'bank_account_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            bank_account = BankAccount.objects.get(pk=bank_account_id)
        except BankAccount.DoesNotExist:
            return Response({'error': 'BankAccount not found.'}, status=status.HTTP_404_NOT_FOUND)

        fmt = None
        if format_name:
            fmt = BankStatementFormat.objects.filter(name=format_name).first()
            if fmt is None:
                known = list(
                    BankStatementFormat.objects.values_list('name', flat=True)[:10]
                )
                return Response(
                    {'error': (
                        f"Statement format '{format_name}' is not set up."
                        + (f" Formats available: {', '.join(known)}." if known
                           else ' Leave the format blank and the columns will be '
                                'read from the file itself.')
                    )},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            csv_text = to_csv_text(csv_file, encoding=(fmt.encoding if fmt else 'utf-8'))
            if fmt is None:
                fmt, skip = build_format_from_file(
                    csv_text, bank_name=bank_account.bank_name or '')
                csv_text = strip_preamble(csv_text, skip)
            importer = BankStatementImporter(fmt)
            stmt = importer.import_csv(
                csv_text, bank_account,
                user=request.user,
                file_name=csv_file.name,
            )
        except StatementFileError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = BankStatementDetailSerializer(stmt, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='reconciliation')
    def reconciliation(self, request, pk=None):
        """
        GET /api/v1/bank-statements/{pk}/reconciliation/

        BANK-007 (Kelvin Kimani / Lefika, 2026-09-15): explains WHY the GL and
        the bank differ. services.get_reconciliation_report already computed
        this correctly (GL cut at the statement date) but nothing exposed it —
        its only caller was a management command.
        """
        from .services import get_reconciliation_report

        stmt = self.get_object()
        rep  = get_reconciliation_report(stmt)
        return Response({
            'statement_id':         stmt.id,
            'statement_date':       stmt.statement_date,
            # None when the bank returned no closing balance — never the
            # string "None", and never a stand-in zero.
            'bank_closing_balance': (None if rep['bank_closing_balance'] is None
                                     else str(rep['bank_closing_balance'])),
            'gl_balance':           str(rep['gl_balance']),
            'difference':           (None if rep['difference'] is None
                                     else str(rep['difference'])),
            'is_reconciled':        rep['is_reconciled'],
            'lines':                rep['lines'],
            'unmatched_lines': BankStatementLineSerializer(
                rep['unmatched_lines'], many=True,
            ).data,
        })

    @action(detail=True, methods=['post'], url_path='run-matching')
    def run_matching(self, request, pk=None):
        """
        POST /api/v1/bank-statements/{pk}/run-matching/
        Runs the auto-matching engine. Returns match counts.
        """
        stmt   = self.get_object()
        engine = ReconciliationEngine(stmt)
        result = engine.run()
        return Response(result)

    @action(detail=True, methods=['post'], url_path='auto-reconcile')
    def auto_reconcile(self, request, pk=None):
        """
        POST /api/v1/bank-statements/{pk}/auto-reconcile/
        Run the rule-based reconciliation engine (banking.rec_engine).

        Body (all optional):
          {"dry_run": true}   -> preview without persisting

        Returns
          {"two_way_matched": N, "rule_matched": N, "still_unmatched": N}
        """
        stmt = self.get_object()
        dry  = bool(request.data.get('dry_run', False))
        try:
            counts = match_statement_lines(stmt, dry_run=dry)
        except Exception as exc:                            # noqa: BLE001
            return Response({'error': str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        if not dry:
            _refresh_recon_state(stmt, user=request.user)
        return Response(counts)

    @action(detail=True, methods=['post'], url_path='approve-reconciliation')
    def approve_reconciliation(self, request, pk=None):
        """
        POST /api/v1/bank-statements/{pk}/approve-reconciliation/
        Second-person approval of a completed reconciliation (BUG b72695a8).

        Segregation of duties:
          * requires the `bank.approve` permission;
          * the approver MUST be a different user from `reconciled_by`;
          * statement must be PENDING_APPROVAL (fully matched, awaiting sign-off).
        Only on approval is the statement marked RECONCILED and the bank
        account's last_reconciled_date stamped.
        """
        from django.utils import timezone
        from core.models import user_has_permission

        stmt = self.get_object()
        if not user_has_permission(request.user, 'bank.approve'):
            return Response(
                {'detail': 'You do not have the bank.approve permission to '
                           'approve a bank reconciliation.'},
                status=status.HTTP_403_FORBIDDEN)
        if stmt.status != BankStatement.Status.PENDING_APPROVAL:
            return Response(
                {'detail': f'Statement is "{stmt.get_status_display()}" — only a '
                           'fully-matched reconciliation awaiting approval can be approved.'},
                status=status.HTTP_400_BAD_REQUEST)
        if stmt.reconciled_by_id and stmt.reconciled_by_id == request.user.pk:
            return Response(
                {'detail': 'Segregation of duties: you reconciled this statement, '
                           'so a different person must approve it.'},
                status=status.HTTP_403_FORBIDDEN)

        stmt.status      = BankStatement.Status.RECONCILED
        stmt.approved_by = request.user
        stmt.approved_at = timezone.now()
        stmt.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        acct = stmt.bank_account
        if (acct.last_reconciled_date is None
                or stmt.statement_date > acct.last_reconciled_date):
            acct.last_reconciled_date = stmt.statement_date
            acct.save(update_fields=['last_reconciled_date', 'updated_at'])
        return Response({
            'status':       stmt.status,
            'approved_by':  request.user.get_username(),
            'reconciled_by': stmt.reconciled_by.get_username() if stmt.reconciled_by_id else None,
        })


class BankStatementLineViewSet(mixins.ListModelMixin,
                                mixins.RetrieveModelMixin,
                                viewsets.GenericViewSet):
    # SECURITY FIX (2026-07-14): had no gate beyond IsAuthenticated, and unlike
    # its sibling viewsets isn't even company-scoped — any employee could read
    # any bank statement's transaction lines by ?statement=<id>.
    permission_classes = [IsAuthenticated, CanViewFinancials]
    queryset = BankStatementLine.objects.select_related(
        'statement__bank_account', 'matched_payment', 'matched_journal_entry'
    ).order_by('statement', 'line_number')
    serializer_class = BankStatementLineSerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['description', 'reference', 'statement__statement_number']
    ordering_fields  = ['transaction_date', 'amount', 'match_status']

    def get_queryset(self):
        qs = super().get_queryset()
        stmt = self.request.query_params.get('statement')
        if stmt:
            qs = qs.filter(statement_id=stmt)
        ms = self.request.query_params.get('match_status')
        if ms:
            qs = qs.filter(match_status=ms)
        # Date-range filter (2026-09-12): the read the three downstream jobs
        # (payment-recon, claims-movement, GENRIC report pack) need — a line
        # lookup by date window, without pulling every statement's lines.
        # Parse before filtering: a raw string goes straight into the ORM and a
        # malformed one ('banana', '2026-13-01') raises inside the queryset and
        # surfaces as a 500. Refuse it as a 400 that says what is wrong.
        date_from = _parse_query_date(self.request.query_params.get('date_from'), 'date_from')
        if date_from:
            qs = qs.filter(transaction_date__gte=date_from)
        date_to = _parse_query_date(self.request.query_params.get('date_to'), 'date_to')
        if date_to:
            qs = qs.filter(transaction_date__lte=date_to)
        return qs

    @action(detail=True, methods=['post'], url_path='match')
    def match(self, request, pk=None):
        """
        POST /api/v1/bank-statement-lines/{pk}/match/
        CFO M6 (2026-09-18): searchable picker with dry-run preview and confidence scoring.

        Body: {
          "payment_id": "uuid" OR "journal_entry_id": "uuid",
          "dry_run": bool (optional, default false)
        }

        Dry-run (dry_run=true):
          - Runs ALL validations without persisting.
          - Returns explanation dict with confidence scoring.
          - DB unchanged, no audit log written.

        Real match (dry_run=false or omitted):
          - Persists the match with computed confidence.
          - Calls _refresh_recon_state.
          - Logs to AuditLog as bank.match_statement_line action.
        """
        from datetime import timedelta
        from decimal import Decimal
        from django.db import transaction as db_tx
        from django.utils import timezone
        from core.models import AuditLog

        line       = self.get_object()
        payment_id = request.data.get('payment_id')
        je_id      = request.data.get('journal_entry_id')
        # Only an explicit yes is a dry run; anything else is a real match.
        dry_run    = str(request.data.get('dry_run', '')).strip().lower() in ('1', 'true', 'yes')

        if not payment_id and not je_id:
            return Response(
                {'error': 'Provide payment_id or journal_entry_id.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if payment_id:
            from payments.models import Payment
            try:
                payment = Payment.objects.get(pk=payment_id)
            except Payment.DoesNotExist:
                return Response({'error': 'Payment not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Entity scope validation (CFO M6): payment company must match line's statement company
            stmt_company_id = line.statement.bank_account.gl_account.owner_company_id
            if payment.company_id and stmt_company_id and payment.company_id != stmt_company_id:
                return Response(
                    {'error': f'Entity mismatch: payment belongs to {payment.company} '
                              f'but statement is in {line.statement.bank_account.gl_account.owner_company}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate amount matches (abs comparison for sign differences)
            if abs(line.amount) != abs(payment.amount):
                return Response(
                    {'error': f'Amount mismatch: statement line {line.amount} vs payment {payment.amount}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Compute confidence: date gap, reference match
            date_gap = abs((payment.payment_date - line.transaction_date).days)
            line_refs = [t.strip().lower() for t in (line.reference, line.description) if (t or '').strip()]
            pay_refs = [t.strip().lower() for t in (payment.reference, payment.payment_number) if (t or '').strip()]
            reference_match = any(a in b or b in a for a in line_refs for b in pay_refs)

            confidence, explanation = compute_match_confidence(
                line.amount, payment.amount, date_gap, reference_match
            )

            if dry_run:
                return Response({
                    'dry_run': True,
                    'explanation': explanation,
                })

            # Real match: save and log
            line.matched_payment    = payment
            line.matched_journal_entry = None

        elif je_id:
            from ledger.models import JournalEntry
            try:
                je = JournalEntry.objects.get(pk=je_id)
            except JournalEntry.DoesNotExist:
                return Response({'error': 'Journal entry not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Entity scope validation: JE company via its lines' accounts
            stmt_company_id = line.statement.bank_account.gl_account.owner_company_id
            je_company_ids = set(
                je.lines.values_list('account__owner_company_id', flat=True).distinct()
            ) - {None}
            if stmt_company_id and je_company_ids and stmt_company_id not in je_company_ids:
                return Response(
                    {'error': f'Entity mismatch: journal entry belongs to a different company.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Compute confidence for JE: use entry_date
            date_gap = abs((je.entry_date - line.transaction_date).days)
            ref_text_line = (line.reference or line.description or '').lower()
            # JE don't have payment_number/reference; use description from lines
            ref_text_je = ' '.join([
                (jel.description or '').lower()
                for jel in je.lines.all()
            ])
            reference_match = bool(ref_text_line and ref_text_je and ref_text_line in ref_text_je)

            # A JE has no single amount to compare, so the amount is NOT scored
            # here (passing 0 would have scored every JE match 0%); the
            # explanation says so rather than claiming the amounts agree.
            confidence, explanation = compute_match_confidence(
                line.amount, line.amount, date_gap, reference_match
            )
            explanation['amount_checked'] = False
            explanation['candidate_amount'] = None

            if dry_run:
                return Response({
                    'dry_run': True,
                    'explanation': explanation,
                })

            # Real match: save and log
            line.matched_journal_entry = je
            line.matched_payment       = None

        line.match_status     = BankStatementLine.MatchStatus.MANUALLY_MATCHED
        line.match_confidence = confidence

        with db_tx.atomic():
            line.save()
            if payment_id:
                # L-BANKAI: learn from the confirmed payment match. Learning is
                # a side benefit — it must never undo the match itself.
                from . import match_memory
                try:
                    with db_tx.atomic():
                        match_memory.remember(line, payment, request.user)
                except Exception:  # noqa: BLE001
                    logger.exception('bank match memory not recorded for line %s', line.pk)

        _refresh_recon_state(line.statement, user=request.user)

        # Audit log: who matched which line to which target, with confidence
        matched_target_id = str(payment.pk if payment_id else je.pk)
        matched_target_type = 'payment' if payment_id else 'journal_entry'
        AuditLog.objects.create(
            table_name='banking.BankStatementLine',
            record_id=str(line.pk),
            action=AuditLog.Action.UPDATE,
            user=request.user,
            ip_address=request.META.get('REMOTE_ADDR'),
            description=f'Manually matched bank line to {matched_target_type} (confidence: {confidence}%)',
            new_values={
                'line_id': str(line.pk),
                'matched_to': matched_target_id,
                'matched_to_type': matched_target_type,
                'confidence': confidence,
                'statement_id': str(line.statement_id),
            },
        )

        serializer = BankStatementLineSerializer(line, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def suggestions(self, request, pk=None):
        """
        GET /api/v1/bank-statement-lines/{pk}/suggestions/
        L-BANKAI: AI-assisted reconciliation suggestions.

        Returns a list of suggested payment matches based on deterministic
        rules and a history of previously confirmed matches.
        """
        from . import match_memory
        from payments.models import Payment

        line = self.get_object()
        # The line list itself is not company-scoped yet; this new read must be,
        # or it would show another company's payees and amounts (Opus judge).
        from core.mixins import apply_company_scope
        in_scope = apply_company_scope(
            request, BankStatementLine.objects.filter(pk=line.pk),
            'statement__bank_account__gl_account__owner_company_id')
        if not in_scope.exists():
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        stmt_company = line.statement.bank_account.gl_account.owner_company
        if not stmt_company:
            return Response({
                'suggestions': [],
                'agrees_with_deterministic': True,
            })

        # Find candidate payments: confirmed, in the same company, not already matched
        candidates = Payment.objects.filter(
            company=stmt_company,
            status=Payment.Status.CONFIRMED,
            bank_statement_lines__isnull=True
        ).select_related('contact', 'company').order_by('-payment_date')[:100]

        suggestion_data = match_memory.suggest(line, list(candidates))

        return Response({
            'line_id': line.pk,
            'suggestions': suggestion_data['suggestions'],
            'agrees_with_deterministic': suggestion_data['agrees_with_deterministic'],
        })

    @action(detail=True, methods=['post'], url_path='create-payment')
    def create_payment(self, request, pk=None):
        """PAY-003 init path #2 — "Create Payment" from a bank-rec line.

        Creates a draft SENT payment from this statement line (amount, date,
        the statement's GL bank account), submits it into the SAME tier
        approval queue as the bill-"Pay" path, and links the line to it
        (matched_payment). Once the approver approves, the payment posts
        DR AP / CR Bank exactly like the bill path. Body:
          {contact_id, reference(optional), payment_method(optional),
           bill_id(optional — allocate to this bill)}
        """
        from decimal import Decimal
        from django.db import transaction as _txn
        from django.core.exceptions import ValidationError as _VErr
        from payments.models import Payment, PaymentAllocation
        from payments.serializers import PaymentDetailSerializer
        from billing.models import Contact, Invoice

        line = self.get_object()
        if line.matched_payment_id:
            return Response(
                {'error': f'Line already linked to payment {line.matched_payment.payment_number}.'},
                status=status.HTTP_400_BAD_REQUEST)
        if line.amount >= 0:
            return Response(
                {'error': 'Create-Payment is for outflow (negative) lines only.'},
                status=status.HTTP_400_BAD_REQUEST)

        contact_id = request.data.get('contact_id')
        if not contact_id:
            return Response({'error': 'contact_id is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            contact = Contact.objects.get(pk=contact_id)
        except Contact.DoesNotExist:
            return Response({'error': 'Contact not found.'}, status=status.HTTP_404_NOT_FOUND)

        bank_acct = line.statement.bank_account
        gl = bank_acct.gl_account
        if gl is None:
            return Response(
                {'error': f'Bank account {bank_acct} has no linked GL account.'},
                status=status.HTTP_400_BAD_REQUEST)

        amount    = abs(line.amount)
        reference = (request.data.get('reference') or line.reference or line.description or '')[:200]
        method    = request.data.get('payment_method') or Payment.PaymentMethod.BANK_TRANSFER
        bill_id   = request.data.get('bill_id')

        try:
            with _txn.atomic():
                payment = Payment(
                    payment_type   = Payment.PaymentType.SENT,
                    contact        = contact,
                    company        = getattr(gl, 'owner_company', None) or getattr(bank_acct, 'company', None),
                    bank_account   = gl,
                    payment_date   = line.transaction_date,
                    currency_code_id = bank_acct.currency_code_id or 'BWP',
                    amount         = amount,
                    payment_method = method,
                    reference      = reference,
                    description    = f"PAY-003 bank-rec payment — stmt line {line.line_number}",
                    created_by     = request.user,
                )
                payment.save(audit_user=request.user)
                if bill_id:
                    try:
                        bill = Invoice.objects.get(pk=bill_id)
                        PaymentAllocation.objects.create(
                            payment=payment, invoice=bill,
                            amount_allocated=min(amount, bill.total_amount or amount))
                    except Invoice.DoesNotExist:
                        pass
                tier, role = payment.submit_for_approval(user=request.user)
                # Link the line to the new payment (pending approval).
                line.matched_payment    = payment
                line.match_status       = BankStatementLine.MatchStatus.MANUALLY_MATCHED
                line.match_confidence   = 100
                line.save()
        except (_VErr, Exception) as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        _refresh_recon_state(line.statement, user=request.user)
        data = PaymentDetailSerializer(payment, context={'request': request}).data
        data['assigned_tier'] = tier
        data['assigned_role'] = role
        data['statement_line_id'] = str(line.pk)
        return Response(data, status=status.HTTP_201_CREATED)


class BankRecRuleViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    """
    Full CRUD for BankRecRule. Scoped via the rule's `company_id` so the
    topbar entity switcher filters correctly.

    SECURITY FIX (2026-07-14): had no gate beyond IsAuthenticated.

    Routes (after `api_router.register('bank-rec-rules', ...)`):
      GET    /api/v1/bank-rec-rules/
      POST   /api/v1/bank-rec-rules/
      GET    /api/v1/bank-rec-rules/<id>/
      PATCH  /api/v1/bank-rec-rules/<id>/
      DELETE /api/v1/bank-rec-rules/<id>/
    """
    permission_classes = [IsAuthenticated, CanViewFinancials]
    company_lookup_field = 'company_id'
    queryset = BankRecRule.objects.select_related(
        'company', 'target_account', 'target_contact', 'created_by',
    ).order_by('priority', 'created_at')
    serializer_class = BankRecRuleSerializer
    filter_backends  = [filters.SearchFilter, filters.OrderingFilter]
    search_fields    = ['description_regex', 'target_account__code',
                        'target_account__name', 'target_contact__name']
    ordering_fields  = ['priority', 'created_at', 'is_active']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
