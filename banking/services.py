"""
banking/services.py

BankStatementImporter   - parse CSV and create BankStatement + BankStatementLine
ReconciliationEngine    - auto-match statement lines to Payments
get_reconciliation_report - summary of bank balance vs GL balance
"""

import csv
import io
from datetime import timedelta
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (
    BankStatement, BankStatementFormat, BankStatementLine,
    compute_line_dedupe_key, line_identity,
)


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')


# ---------------------------------------------------------------------------
# BankStatementImporter
# ---------------------------------------------------------------------------

class BankStatementImporter:
    """
    Parse a CSV bank statement and persist BankStatement + BankStatementLine.

    Usage::

        fmt       = BankStatementFormat.objects.get(name='FNB BWP Current Account')
        importer  = BankStatementImporter(fmt)
        statement = importer.import_csv(csv_text, bank_account, user=request.user)
    """

    def __init__(self, fmt: BankStatementFormat):
        self.fmt = fmt

    # ------------------------------------------------------------------ #

    @transaction.atomic
    def import_csv(self, csv_text: str, bank_account, *,
                   user=None, file_name: str = 'statement.csv') -> BankStatement:
        """
        Parse *csv_text* and return a persisted :class:`~banking.models.BankStatement`.

        ``csv_text``    - raw CSV content as a string
        ``bank_account`` - a :class:`~banking.models.BankAccount` instance
        """
        fmt    = self.fmt
        reader = csv.DictReader(io.StringIO(csv_text), delimiter=fmt.delimiter)
        rows   = list(reader)

        # Skip leading non-data preamble rows (after the header)
        rows = rows[fmt.skip_rows:]

        if not rows:
            raise ValueError('No data rows found in CSV.')

        def _col(row, col_name):
            """Return stripped value for col_name, matched case-insensitively."""
            if not col_name:
                return None
            for k, v in row.items():
                if k and k.strip().lower() == col_name.strip().lower():
                    return v.strip() if v else v
            return None

        parsed = []
        for i, row in enumerate(rows, start=1):
            row_data = self._parse_row(i, row, _col)
            if row_data:
                parsed.append(row_data)

        if not parsed:
            # Bug 20b32822: this used to be the whole message. Every row had been
            # dropped by _parse_row for one of two silent reasons — no value in the
            # date column, or a date that would not parse — and the person uploading
            # was told nothing they could act on. Show what we looked for and what
            # we actually found on the first row.
            # Panel review flagged the first draft of this message: it echoed the
            # whole first row of a BANK STATEMENT into the error (and the logs),
            # which can carry customer names, account numbers and payment
            # references. Name the COLUMNS we found and show only the value from
            # the date column — a date is not personal data. Never the row.
            sample = rows[0] if rows else {}
            headers = ', '.join(k for k in list(sample.keys())[:10] if k) or '(none)'
            date_val = (sample.get(fmt.date_column) or '').strip() if fmt.date_column else ''
            seen = f' The first date value we read was "{date_val}".' if date_val else ''
            raise ValueError(
                f'None of the {len(rows)} rows in this file could be read. '
                f'We looked for a date in the "{fmt.date_column}" column formatted '
                f'as {fmt.date_format}.{seen} Columns found in the file: {headers}. '
                f'Check the date column matches and that the file covers the period '
                f'you expect.'
            )

        # BUG-004: reject future-dated statements / lines. A bank statement can
        # never carry a transaction dated after today — those lines never match
        # a real cleared transaction and silently break the reconciliation.
        # "Today" is BOTSWANA's today (settings.TIME_ZONE), never the server
        # clock's: date.today() is the UTC date on a UTC box, so at 01:00
        # Gaborone every line dated today looked future-dated and the WHOLE
        # upload was refused. Same midnight window as the JE guards (f9ff6d56).
        from django.utils import timezone as _tz
        _today = _tz.localdate()
        _future = [r for r in parsed if r.get('transaction_date') and r['transaction_date'] > _today]
        if _future:
            n = len(_future)
            worst = max(r['transaction_date'] for r in _future)
            raise ValueError(
                f'Statement contains {n} transaction line(s) dated in the future '
                f'(latest {worst:%d-%b-%Y}; today is {_today:%d-%b-%Y}). '
                f'A bank statement cannot contain dates that have not happened yet. '
                f'Correct the value dates and re-upload.'
            )

        # Opening / closing balances from first / last running-balance values
        opening_balance = ZERO
        closing_balance = ZERO

        last_balance = parsed[-1].get('running_balance')
        if last_balance is not None:
            closing_balance = last_balance

        first = parsed[0]
        if first.get('running_balance') is not None:
            opening_balance = (
                first['running_balance'] - first['amount']
            ).quantize(TWO_PLACES)

        statement_date = parsed[-1]['transaction_date']

        # Premortem 2026-06-10: block accidental re-upload of an
        # already-imported statement. Re-importing the same file silently
        # doubles the lines, inflating the reconciliation and risking
        # double-matching of cash. Fingerprint = bank account + statement
        # date + closing balance + line count.
        _dupe = BankStatement.objects.filter(
            bank_account    = bank_account,
            statement_date  = statement_date,
            closing_balance = closing_balance,
            line_count      = len(parsed),
        ).first()
        if _dupe is not None:
            raise ValueError(
                f"This statement appears already imported as "
                f"{_dupe.statement_number} ({len(parsed)} lines, closing "
                f"{closing_balance}). Re-importing would duplicate every line. "
                f"Delete the existing statement first if you really need to re-import."
            )

        statement = BankStatement(
            bank_account    = bank_account,
            statement_date  = statement_date,
            opening_balance = opening_balance,
            closing_balance = closing_balance,
            file_name       = file_name,
            imported_by     = user,
            line_count      = len(parsed),
        )
        statement.save(audit_user=user)

        # No-double-import guard (2026-09-12): skip any line whose content
        # fingerprint already exists rather than crashing on the DB's unique
        # constraint. Checked as a set up front — one query, not N.
        #
        # Keyed on an OCCURRENCE count (how many times this exact content has
        # already been seen), NOT the row's line_number/position — an
        # overlapping re-pull (e.g. 1-15 Sep, then 1-30 Sep) puts the same
        # transaction at a DIFFERENT line_number the second time, so keying
        # on position would miss it. occurrence_counts resets per import; a
        # transaction that is the only occurrence of its content in THIS
        # import is always occurrence 0, so it collides correctly with an
        # already-imported occurrence-0 line from an earlier overlapping pull.
        existing_keys = set(BankStatementLine.objects.filter(
            statement__bank_account=bank_account,
        ).values_list('dedupe_key', flat=True))
        occurrence_counts: dict = {}
        duplicate_lines = 0
        for row_data in parsed:
            identity = line_identity(
                bank_account.id, row_data['transaction_date'], row_data['amount'],
                row_data['description'], row_data.get('reference') or '',
            )
            occurrence = occurrence_counts.get(identity, 0)
            occurrence_counts[identity] = occurrence + 1
            key = compute_line_dedupe_key(
                bank_account.id,
                row_data['transaction_date'],
                row_data['amount'],
                row_data['description'],
                row_data.get('reference') or '',
                occurrence=occurrence,
            )
            if key in existing_keys:
                duplicate_lines += 1
                continue
            existing_keys.add(key)
            BankStatementLine.objects.create(
                statement        = statement,
                line_number      = row_data['line_number'],
                transaction_date = row_data['transaction_date'],
                description      = row_data['description'],
                reference        = row_data.get('reference') or '',
                amount           = row_data['amount'],
                running_balance  = row_data.get('running_balance'),
                raw_data         = row_data.get('raw_data'),
                dedupe_key       = key,
            )

        if duplicate_lines:
            import logging
            logging.getLogger(__name__).info(
                'Statement %s: skipped %d duplicate line(s) already present.',
                statement.statement_number, duplicate_lines,
            )
            # line_count must equal rows actually stored, not rows merely
            # parsed — a duplicate skipped above is not a line on this
            # statement (the banking page and the FNB toast both render
            # this number as-is).
            statement.line_count = len(parsed) - duplicate_lines
            statement.save(update_fields=['line_count'])

        return statement

    # ------------------------------------------------------------------ #

    def _parse_row(self, line_number, row, _col):
        fmt = self.fmt

        raw_date = _col(row, fmt.date_column)
        if not raw_date:
            return None
        try:
            txn_date = datetime.strptime(raw_date, fmt.date_format).date()
        except ValueError:
            return None

        description = _col(row, fmt.description_column) or ''
        reference   = _col(row, fmt.reference_column) if fmt.reference_column else None

        amount = self._parse_amount(row, _col)
        if amount is None:
            return None

        running_balance = None
        if fmt.balance_column:
            raw_bal = _col(row, fmt.balance_column)
            if raw_bal:
                try:
                    running_balance = Decimal(
                        raw_bal.replace(',', '')
                    ).quantize(TWO_PLACES)
                except InvalidOperation:
                    pass

        return {
            'line_number':      line_number,
            'transaction_date': txn_date,
            'description':      description,
            'reference':        reference,
            'amount':           amount,
            'running_balance':  running_balance,
            'raw_data':         dict(row),
        }

    def _parse_amount(self, row, _col):
        """Return a signed Decimal. Positive = inflow, Negative = outflow."""
        fmt = self.fmt

        if fmt.amount_column:
            raw = _col(row, fmt.amount_column)
            if not raw:
                return None
            try:
                val = Decimal(raw.replace(',', '')).quantize(TWO_PLACES)
            except InvalidOperation:
                return None
            if fmt.sign_convention == BankStatementFormat.SignConvention.DEBIT_POSITIVE:
                val = -val
            return val

        if fmt.debit_column and fmt.credit_column:
            raw_dr = _col(row, fmt.debit_column) or '0'
            raw_cr = _col(row, fmt.credit_column) or '0'
            try:
                dr = Decimal(raw_dr.replace(',', '')).quantize(TWO_PLACES)
                cr = Decimal(raw_cr.replace(',', '')).quantize(TWO_PLACES)
            except InvalidOperation:
                return None
            return (cr - dr).quantize(TWO_PLACES)

        return None


# ---------------------------------------------------------------------------
# ReconciliationEngine
# ---------------------------------------------------------------------------

class ReconciliationEngine:
    """
    Auto-match unmatched BankStatementLines against confirmed Payments.

    Rules (applied in priority order, first match wins):
      Rule 1 — exact reference + effective amount  (95% confidence)
      Rule 2 — effective amount + date within 3 days (80% confidence)
      Rule 3 — effective amount only               (60% confidence)

    Lines are auto-matched for any confidence >= 60.

    Effective amount for broker SENT payments:
      ``payment.amount_bwp - wht_record.wht_amount``
    because WHT is withheld before transfer so the bank shows only the net.
    """

    CONFIDENCE_RULE1 = 95
    CONFIDENCE_RULE2 = 80
    CONFIDENCE_RULE3 = 60

    def __init__(self, statement: BankStatement):
        self.statement = statement

    @transaction.atomic
    def run(self) -> dict:
        """
        Match all UNMATCHED lines in this statement.
        Returns ``{'matched': N, 'unmatched': N, 'total': N}``.
        """
        from payments.models import Payment

        unmatched = list(
            self.statement.lines
            .filter(match_status=BankStatementLine.MatchStatus.UNMATCHED)
            .select_related('statement__bank_account')
        )

        if not unmatched:
            return {'matched': 0, 'unmatched': 0, 'total': 0}

        bank_gl = self.statement.bank_account.gl_account

        payments = list(
            Payment.objects.filter(
                bank_account=bank_gl,
                status__in=[
                    Payment.Status.CONFIRMED,
                    Payment.Status.RECONCILED,
                ],
            ).select_related('contact', 'wht_record')
        )

        # Reference -> [Payment] lookup (normalised to uppercase)
        payment_by_ref = {}
        for p in payments:
            key = (p.reference or '').strip().upper()
            if key:
                payment_by_ref.setdefault(key, []).append(p)

        matched_count = 0
        for line in unmatched:
            result = self._match_line(line, payments, payment_by_ref)
            if result:
                payment, confidence = result
                line.match_status     = BankStatementLine.MatchStatus.AUTO_MATCHED
                line.matched_payment  = payment
                line.match_confidence = confidence
                line.save()
                matched_count += 1

        total = len(unmatched)
        return {
            'matched':   matched_count,
            'unmatched': total - matched_count,
            'total':     total,
        }

    # ------------------------------------------------------------------ #

    def _effective_amount(self, payment) -> Decimal:
        """
        Amount that should appear on the bank statement (net of WHT if applicable).
        """
        try:
            wht = payment.wht_record
            return (payment.amount_bwp - wht.wht_amount).quantize(TWO_PLACES)
        except Exception:
            return payment.amount_bwp.quantize(TWO_PLACES)

    def _sign_matches(self, line_amount: Decimal, payment) -> bool:
        """Inflow (positive line) matches RECEIVED; outflow (negative) matches SENT."""
        from payments.models import Payment as P
        if line_amount > ZERO:
            return payment.payment_type == P.PaymentType.RECEIVED
        return payment.payment_type == P.PaymentType.SENT

    def _match_line(self, line, all_payments, payment_by_ref):
        """Return (Payment, confidence) or None."""
        line_abs   = abs(line.amount)
        candidates = [
            p for p in all_payments if self._sign_matches(line.amount, p)
        ]

        # Rule 1 — exact reference + effective amount
        if line.reference:
            ref_key = line.reference.strip().upper()
            # An Omni-loaded payment's endToEndId carries an origin marker like
            # " (O)" or " (UNI)" (CFO 2026-08-22 / 2026-09-10); the bank echoes
            # it into the statement line. Strip any trailing marker so the
            # exact-reference match still ties to our internal reference.
            import re as _re
            ref_key = _re.sub(r'\s*\([A-Z]{1,4}\)$', '', ref_key).strip()
            for p in payment_by_ref.get(ref_key, []):
                if self._sign_matches(line.amount, p):
                    if self._effective_amount(p) == line_abs:
                        return (p, self.CONFIDENCE_RULE1)

        # Rule 2 — effective amount + date within 3 days
        for p in candidates:
            if self._effective_amount(p) == line_abs:
                delta = abs((p.payment_date - line.transaction_date).days)
                if delta <= 3:
                    return (p, self.CONFIDENCE_RULE2)

        # Rule 3 — effective amount only
        for p in candidates:
            if self._effective_amount(p) == line_abs:
                return (p, self.CONFIDENCE_RULE3)

        return None


# ---------------------------------------------------------------------------
# Reconciliation report
# ---------------------------------------------------------------------------

def get_reconciliation_report(statement: BankStatement) -> dict:
    """
    Return a summary dict comparing the bank's closing balance to the GL.

    Structure::

        {
            'statement':            BankStatement,
            'bank_closing_balance': Decimal,
            'gl_balance':           Decimal,
            'difference':           Decimal,   # bank - GL
            'is_reconciled':        bool,
            'lines': {
                'total': int, 'auto_matched': int,
                'manually_matched': int, 'excluded': int, 'unmatched': int,
            },
            'unmatched_lines': QuerySet[BankStatementLine],
        }
    """
    from django.db.models import Sum
    from ledger.models import JournalEntry, JournalEntryLine

    lines = statement.lines.all()

    counts = {
        status: lines.filter(match_status=status).count()
        for status in [
            BankStatementLine.MatchStatus.UNMATCHED,
            BankStatementLine.MatchStatus.AUTO_MATCHED,
            BankStatementLine.MatchStatus.MANUALLY_MATCHED,
            BankStatementLine.MatchStatus.EXCLUDED,
        ]
    }

    # GL balance for the bank's GL account: sum of posted JE lines up to statement_date
    bank_gl_account = statement.bank_account.gl_account
    agg = JournalEntryLine.objects.filter(
        account=bank_gl_account,
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__lte=statement.statement_date,
    ).aggregate(total_dr=Sum('debit_bwp'), total_cr=Sum('credit_bwp'))

    total_dr = agg['total_dr'] or ZERO
    total_cr = agg['total_cr'] or ZERO

    # Asset account: normal balance = Dr - Cr
    gl_balance   = (total_dr - total_cr).quantize(TWO_PLACES)
    # None when the bank returned no closing balance (2026-09-20). There is no
    # difference to state against a figure we do not have, and a statement
    # with no bank balance is never "reconciled".
    bank_balance = statement.closing_balance
    difference   = (None if bank_balance is None
                    else (bank_balance - gl_balance).quantize(TWO_PLACES))

    return {
        'statement':            statement,
        'bank_closing_balance': bank_balance,
        'gl_balance':           gl_balance,
        'difference':           difference,
        'is_reconciled':        difference == ZERO,
        'lines': {
            'total':            lines.count(),
            'auto_matched':     counts[BankStatementLine.MatchStatus.AUTO_MATCHED],
            'manually_matched': counts[BankStatementLine.MatchStatus.MANUALLY_MATCHED],
            'excluded':         counts[BankStatementLine.MatchStatus.EXCLUDED],
            'unmatched':        counts[BankStatementLine.MatchStatus.UNMATCHED],
        },
        'unmatched_lines': lines.filter(
            match_status=BankStatementLine.MatchStatus.UNMATCHED
        ),
    }
