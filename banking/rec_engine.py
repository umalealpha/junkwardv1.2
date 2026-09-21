"""
banking/rec_engine.py

Bank reconciliation rule engine.

Two-stage matcher for every UNMATCHED BankStatementLine on a statement:

  1. Two-way match against POSTED JournalEntryLines on the bank account
     where |amount - line.amount| < 0.01 and the JE entry_date is within
     +/-3 days of the line's transaction_date. On hit, sets
     line.match_status = 'auto_matched' and links the JE.

  2. Rule-based match against BankRecRule in ascending `priority`. On hit:
       * action='auto_je'  -> builds a DRAFT JournalEntry with two lines
                              (bank GL + rule.target_account)
       * action='propose'  -> just tags the line; no JE created
     In both cases sets line.match_status = 'auto_matched' and
     match_confidence so the UI can colour-code rule-matched lines.

Public entry point::

    from banking.rec_engine import match_statement_lines
    counts = match_statement_lines(statement, dry_run=False)
    # -> {'two_way_matched': N, 'rule_matched': N, 'still_unmatched': N}
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from .models import BankStatement, BankStatementLine, BankRecRule


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')

# Confidence scores so the existing reporting / UI legend can distinguish
# the two paths. Mirrors the legacy ReconciliationEngine numbering.
CONF_TWO_WAY      = 90   # exact GL-side match, within 3-day window
CONF_RULE_AUTO_JE = 75   # rule fired, draft JE booked
CONF_RULE_PROPOSE = 50   # rule fired, line tagged but no JE


def _amounts_equal(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) < TWO_PLACES


def _amount_in_bounds(amount: Decimal,
                      lo: Decimal | None, hi: Decimal | None) -> bool:
    if lo is not None and amount < lo:
        return False
    if hi is not None and amount > hi:
        return False
    return True


def _find_two_way_match(line: BankStatementLine, bank_gl):
    """
    Return a JournalEntry whose POSTED lines have a debit_bwp or credit_bwp on
    the bank GL matching |line.amount|, with entry_date within +/-3 days.
    None on no match.

    Sign convention on the bank GL:
      * line.amount > 0 (inflow) -> bank GL debited (cash arrived) -> debit_bwp > 0
      * line.amount < 0 (outflow) -> bank GL credited                -> credit_bwp > 0
    """
    from ledger.models import JournalEntry, JournalEntryLine

    target_abs = abs(line.amount).quantize(TWO_PLACES)
    window_lo = line.transaction_date - timedelta(days=3)
    window_hi = line.transaction_date + timedelta(days=3)

    qs = JournalEntryLine.objects.filter(
        account=bank_gl,
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__gte=window_lo,
        journal_entry__entry_date__lte=window_hi,
    ).select_related('journal_entry')

    if line.amount > ZERO:
        # Bank should be debited
        qs = qs.filter(
            debit_bwp__gte=target_abs - TWO_PLACES,
            debit_bwp__lt=target_abs + TWO_PLACES,
        )
    else:
        qs = qs.filter(
            credit_bwp__gte=target_abs - TWO_PLACES,
            credit_bwp__lt=target_abs + TWO_PLACES,
        )

    # Exclude JEs already linked to a different statement line — first-come,
    # first-served avoids double-matching when two lines have identical amounts
    # on the same day.
    already_linked = (
        BankStatementLine.objects
        .exclude(pk=line.pk)
        .filter(matched_journal_entry__isnull=False)
        .values_list('matched_journal_entry_id', flat=True)
    )
    qs = qs.exclude(journal_entry_id__in=already_linked)

    jel = qs.first()
    return jel.journal_entry if jel else None


def _evaluate_rule(rule: BankRecRule, line: BankStatementLine,
                   compiled_re: re.Pattern) -> bool:
    """Return True iff *line* satisfies *rule* (regex + amount bounds)."""
    if not compiled_re.search(line.description or ''):
        return False
    if not _amount_in_bounds(line.amount, rule.amount_min, rule.amount_max):
        return False
    return True


def _build_draft_je_for_rule(rule: BankRecRule, line: BankStatementLine,
                             bank_gl):
    """
    Build (but do not post) a balanced DRAFT JournalEntry for *line* using
    *rule*.target_account as the counter leg.

      line.amount > 0 (inflow)  -> Dr bank_gl, Cr rule.target_account
      line.amount < 0 (outflow) -> Dr rule.target_account, Cr bank_gl

    Returns the JournalEntry instance.
    """
    from ledger.models import JournalEntry, JournalEntryLine

    amt = abs(line.amount).quantize(TWO_PLACES)
    je = JournalEntry.objects.create(
        entry_date       = line.transaction_date,
        description      = (
            f"Bank rec rule [{rule.priority}] {rule.description_regex} -> "
            f"{rule.target_account.code} (line {line.line_number})"
        )[:500],
        source_type      = 'bank_rec_rule',
        source_id        = rule.id,
        journal_type     = JournalEntry.JournalType.BANK,
        currency_code_id = 'BWP',
        exchange_rate    = Decimal('1.00000000'),
        company          = rule.company,
        created_by       = rule.created_by,
        status           = JournalEntry.Status.DRAFT,
        is_related_party = False,
    )

    if line.amount > ZERO:
        # Inflow: Dr bank, Cr counter
        JournalEntryLine.objects.create(
            journal_entry=je, account=bank_gl,
            description=f"Bank inflow {line.description[:200]}",
            debit_amount=amt,  credit_amount=ZERO,
            debit_bwp=amt,     credit_bwp=ZERO,
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=rule.target_account,
            description=f"Rule counter {rule.description_regex}",
            debit_amount=ZERO, credit_amount=amt,
            debit_bwp=ZERO,    credit_bwp=amt,
            contact=rule.target_contact,
        )
    else:
        # Outflow: Dr counter, Cr bank
        JournalEntryLine.objects.create(
            journal_entry=je, account=rule.target_account,
            description=f"Rule counter {rule.description_regex}",
            debit_amount=amt,  credit_amount=ZERO,
            debit_bwp=amt,     credit_bwp=ZERO,
            contact=rule.target_contact,
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=bank_gl,
            description=f"Bank outflow {line.description[:200]}",
            debit_amount=ZERO, credit_amount=amt,
            debit_bwp=ZERO,    credit_bwp=amt,
        )
    return je


@transaction.atomic
def match_statement_lines(statement: BankStatement,
                          *, dry_run: bool = False) -> dict:
    """
    Run the two-stage matcher across every UNMATCHED line in *statement*.

    Returns::

        {
            'two_way_matched': int,
            'rule_matched':    int,
            'still_unmatched': int,
        }

    When *dry_run* is True, no JEs are created and no line.match_status
    mutations are persisted (the surrounding transaction is rolled back at
    the end). Useful for the "preview matches" UI button.
    """
    counts = {'two_way_matched': 0, 'rule_matched': 0, 'still_unmatched': 0}

    unmatched = list(
        statement.lines
        .filter(match_status=BankStatementLine.MatchStatus.UNMATCHED)
        .select_related('statement__bank_account__gl_account')
        .order_by('line_number')
    )
    if not unmatched:
        return counts

    bank_gl       = statement.bank_account.gl_account
    bank_company  = getattr(bank_gl, 'owner_company_id', None)

    # Compile every rule's regex once. Skip rules whose regex won't compile.
    rules = list(
        BankRecRule.objects
        .filter(is_active=True, company_id=bank_company)
        .select_related('target_account', 'target_contact', 'created_by',
                        'company')
        .order_by('priority', 'created_at')
    )
    compiled: list[tuple[BankRecRule, re.Pattern]] = []
    for r in rules:
        try:
            compiled.append((r, re.compile(r.description_regex, re.IGNORECASE)))
        except re.error:
            # Bad regex on a rule shouldn't blow up the whole run.
            continue

    for line in unmatched:
        # ---- Stage 1: two-way match ------------------------------------
        je = _find_two_way_match(line, bank_gl)
        if je is not None:
            line.matched_journal_entry = je
            line.match_status     = BankStatementLine.MatchStatus.AUTO_MATCHED
            line.match_confidence = CONF_TWO_WAY
            line.notes = ((line.notes or '') +
                          f"\n[auto] two-way matched to JE {je.entry_number}").strip()
            line.save()
            counts['two_way_matched'] += 1
            continue

        # ---- Stage 2: rule-based --------------------------------------
        matched_rule = None
        for rule, pat in compiled:
            if _evaluate_rule(rule, line, pat):
                matched_rule = rule
                break

        if matched_rule is None:
            counts['still_unmatched'] += 1
            continue

        if matched_rule.action == BankRecRule.Action.AUTO_JE:
            draft_je = _build_draft_je_for_rule(matched_rule, line, bank_gl)
            line.matched_journal_entry = draft_je
            line.match_confidence      = CONF_RULE_AUTO_JE
            line.notes = ((line.notes or '') +
                          f"\n[auto] rule {matched_rule.priority} -> draft JE "
                          f"{draft_je.entry_number}").strip()
        else:
            line.match_confidence = CONF_RULE_PROPOSE
            line.notes = ((line.notes or '') +
                          f"\n[propose] rule {matched_rule.priority} -> "
                          f"{matched_rule.target_account.code}").strip()

        line.match_status = BankStatementLine.MatchStatus.AUTO_MATCHED
        line.save()
        counts['rule_matched'] += 1

    if dry_run:
        transaction.set_rollback(True)

    return counts
