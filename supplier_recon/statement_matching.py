"""supplier_recon/statement_matching.py — reconcile a parsed supplier statement
against the vendor bills Omni already holds for that supplier month, and propose
what to pay.

This is the decision half of the front end. It never moves money: it writes
``StatementMatch`` rows carrying, per line, one of Pay now / Hold / Do not pay /
Investigate, a proposed amount, and a plain reason. The CFO authorises the
actual payment in FNB.

Hard safety rule (usability test 2026-08-24): a line is NEVER proposed for
payment when it is unmatched, duplicated, unapproved, or already paid. Encoded
here and pinned by tests. "Approved" means the bill passed Omni's own 3-way
match (``MatchStatus.MATCHED``); anything short of that (no PO, no goods
receipt, no claim authority, only partially matched) is a Hold with the reason
named, never an automatic Pay now.
"""
from __future__ import annotations

import re
from decimal import Decimal

from django.utils import timezone

from .constants import (
    MatchStatus,
    NEVER_PAY_MATCH_TYPES,
    PaymentProposal,
    PaymentStatus,
    STATEMENT_AMOUNT_TOLERANCE,
    StatementLineType,
    StatementStatus,
    StmtMatchType,
    ZERO,
)
from .statement_models import StatementMatch

# match_status -> the reason a bill cannot yet be paid (drives the Hold text).
_HOLD_REASON = {
    MatchStatus.NO_PO:     'No purchase order — no authority to spend.',
    MatchStatus.NO_CLAIM:  'No claim authorisation on the PO.',
    MatchStatus.NO_GRN:    'No posted goods receipt — nothing proves it arrived.',
    MatchStatus.PARTIAL:   'PO match has a price/quantity variance.',
    MatchStatus.UNMATCHED: 'Bill is unmatched — no PO/claim to authorise it.',
}


def _norm_ref(ref) -> str:
    """Reference key for matching: alphanumerics only, upper-cased."""
    return re.sub(r'[^A-Za-z0-9]', '', str(ref or '')).upper()


# Fuzzy suffix/prefix matching only kicks in for references this long, and only
# when it resolves to exactly ONE bill. A 4-char tail like "1001" could claim
# "INV-21001" — so we fail closed (return None → the line is treated as
# statement-only / investigate, never a silent wrong-bill payment). L16.
_MIN_FUZZY_KEY = 5


def _find_bill(stmt_ref: str, bills_by_ref: dict, all_bills: list):
    """Exact normalised-reference match, else a conservative suffix/prefix
    fallback that must resolve to exactly one bill. Ambiguity → None (fail
    closed), so an unmatched line is investigated, never auto-paid."""
    key = _norm_ref(stmt_ref)
    if not key:
        return None
    if key in bills_by_ref:
        return bills_by_ref[key]
    if len(key) < _MIN_FUZZY_KEY:
        return None
    candidates = [item for bkey, item in bills_by_ref.items()
                  if len(bkey) >= _MIN_FUZZY_KEY
                  and (bkey.endswith(key) or key.endswith(bkey))]
    return candidates[0] if len(candidates) == 1 else None


def match_statement(statement) -> dict:
    """(Re)compute StatementMatch rows for one statement. Idempotent: clears and
    rebuilds its own matches, so a rerun on the same statement is safe."""
    line = statement.line
    bills = list(line.items.select_related('invoice').all())

    bills_by_ref = {}
    for b in bills:
        k = _norm_ref(b.invoice.invoice_number)
        # First bill wins a reference; a genuine Omni duplicate is a separate
        # concern handled by the run build, not here.
        bills_by_ref.setdefault(k, b)

    stmt_lines = list(statement.lines.all())

    # Duplicate detection on the STATEMENT: an invoice reference that appears on
    # more than one statement invoice line.
    ref_counts = {}
    for sl in stmt_lines:
        if sl.line_type == StatementLineType.INVOICE and sl.reference:
            k = _norm_ref(sl.reference)
            ref_counts[k] = ref_counts.get(k, 0) + 1

    matches = []
    matched_bill_ids = set()
    seen_dup_refs = set()

    for sl in stmt_lines:
        rec = None
        variance = ZERO
        proposal = PaymentProposal.INVESTIGATE
        proposed = ZERO
        reason = ''
        mtype = StmtMatchType.STATEMENT_ONLY

        if sl.line_type == StatementLineType.BALANCE_FWD:
            continue  # anchors the totals; not a payable line

        if sl.line_type == StatementLineType.CREDIT_NOTE:
            mtype = StmtMatchType.CREDIT_NOTE
            proposal = PaymentProposal.DO_NOT_PAY
            reason = 'Credit note — reduces the balance, never a payment out.'

        elif sl.line_type == StatementLineType.PAYMENT:
            mtype = StmtMatchType.UNMATCHED_PAYMENT
            proposal = PaymentProposal.INVESTIGATE
            reason = ('Supplier shows a payment received — confirm it matches an '
                      'Omni payment; never pay it again.')

        elif sl.line_type == StatementLineType.INVOICE:
            key = _norm_ref(sl.reference)
            is_dup = ref_counts.get(key, 0) > 1
            rec = _find_bill(sl.reference, bills_by_ref, bills)

            if is_dup:
                mtype = StmtMatchType.DUPLICATE
                proposal = PaymentProposal.DO_NOT_PAY
                reason = f'Reference {sl.reference} appears more than once on the statement.'
                seen_dup_refs.add(key)
            elif rec is None:
                mtype = StmtMatchType.STATEMENT_ONLY
                proposal = PaymentProposal.INVESTIGATE
                reason = 'On the statement but no matching bill in Omni.'
            elif rec.id in matched_bill_ids:
                # A second statement line resolving to a bill already consumed by
                # an earlier line — treat as a duplicate, never a second Pay-now.
                mtype = StmtMatchType.DUPLICATE
                proposal = PaymentProposal.DO_NOT_PAY
                reason = 'This Omni bill already matched an earlier statement line.'
            else:
                matched_bill_ids.add(rec.id)
                variance = (sl.amount or ZERO) - (rec.amount or ZERO)
                # Outstanding for the pay-safety gate is the SMALLER of the
                # period-snapshot outstanding and the LIVE invoice outstanding —
                # so a bill paid after the run was built (snapshot still shows it
                # owing) is caught as already-paid and never proposed for payment.
                snap_out = rec.amount_outstanding
                live_out = ((rec.invoice.total_amount or ZERO)
                            - (rec.invoice.amount_paid or ZERO))
                outstanding = min(snap_out, live_out)
                if outstanding <= ZERO:
                    mtype = StmtMatchType.ALREADY_PAID
                    proposal = PaymentProposal.DO_NOT_PAY
                    reason = 'Bill is already fully paid in Omni.'
                elif abs(variance) > STATEMENT_AMOUNT_TOLERANCE:
                    mtype = StmtMatchType.AMOUNT_VARIANCE
                    proposal = PaymentProposal.INVESTIGATE
                    reason = (f'Statement {sl.amount} vs Omni {rec.amount} '
                              f'(variance {variance:+}).')
                else:
                    mtype = StmtMatchType.MATCHED
                    if rec.payment_status == PaymentStatus.HELD:
                        # Payables deliberately held this bill (funds / dispute /
                        # salvage). The statement panel must not contradict that.
                        proposal = PaymentProposal.HOLD
                        reason = 'Bill is on hold in Omni — clear the hold first.'
                    elif rec.match_status == MatchStatus.MATCHED:
                        proposal = PaymentProposal.PAY_NOW
                        proposed = outstanding
                        reason = 'Matches Omni and passed the 3-way match.'
                    else:
                        proposal = PaymentProposal.HOLD
                        reason = _HOLD_REASON.get(
                            rec.match_status, 'Not yet approved to pay.')
        else:
            continue  # OTHER — not a payable line

        # Safety net: nothing on the never-pay list may carry Pay now.
        if mtype in NEVER_PAY_MATCH_TYPES and proposal == PaymentProposal.PAY_NOW:
            proposal = PaymentProposal.INVESTIGATE
            reason = (reason + ' [blocked from auto-pay]').strip()

        matches.append(StatementMatch(
            statement=statement, statement_line=sl, recon_item=rec,
            match_type=mtype, variance=variance, proposal=proposal,
            proposed_amount=proposed, reason=reason))

    # Omni bills the supplier did NOT list — real obligations to check, never an
    # automatic pay (the supplier isn't even claiming them this month).
    for b in bills:
        if b.id in matched_bill_ids:
            continue
        matches.append(StatementMatch(
            statement=statement, statement_line=None, recon_item=b,
            match_type=StmtMatchType.OMNI_ONLY, variance=ZERO,
            proposal=PaymentProposal.INVESTIGATE, proposed_amount=ZERO,
            reason='In Omni but not on the supplier statement.'))

    # Persist idempotently.
    statement.matches.all().delete()
    StatementMatch.objects.bulk_create(matches)
    statement.status = StatementStatus.MATCHED
    statement.matched_at = timezone.now()
    statement.save(update_fields=['status', 'matched_at', 'updated_at'])

    return summarise(matches)


def summarise(matches) -> dict:
    """Counts + proposed-pay total, for the API response and the board."""
    by_type, by_proposal = {}, {}
    pay_total = ZERO
    for mm in matches:
        by_type[mm.match_type] = by_type.get(mm.match_type, 0) + 1
        by_proposal[mm.proposal] = by_proposal.get(mm.proposal, 0) + 1
        if mm.proposal == PaymentProposal.PAY_NOW:
            pay_total += (mm.proposed_amount or ZERO)
    return {
        'total_lines': len(matches),
        'by_match_type': {str(k): v for k, v in by_type.items()},
        'by_proposal': {str(k): v for k, v in by_proposal.items()},
        'proposed_pay_total': str(pay_total),
    }
