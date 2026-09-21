"""
banking/line_references.py — CR-001: what a bank statement line was FOR.

A `BankStatementLine` has always carried `description` and a generic
`reference`. Neither says, in a field anything can read, WHICH CLAIM the money
settled or WHICH SUPPLIER INVOICE it paid. Every consumer that needed that had
to re-parse free text for itself, and the B6 Claims Payment Movement Report
(claims/payment_movement.py) cannot exist at all without it: it has to split
supplier-invoice settlements from payments to individual claimants, and it has
to know which lines are INVOICE basis, because only those may be de-grossed
for VAT.

So this module is the ONE place that reads a line's text and says what it was
for. It is pure Python — no ORM, no Django, no I/O — so it can be unit-tested
directly with real narrations, and so the same function serves the import path
(`BankStatementLine.save()`), the re-runnable backfill command and the report.

WHAT IT READS
-------------
The house wording rules are already written down in
`taskboard/narration_templates.py`, to the format Finance gave on 2026-08-20:

    Claims          <claim number> <PAYMENT TYPE>   e.g. "G2026004512 AOL"
    Repairs         ALPHA DIRECT <claim number> <invoice number>
    Supplier inv.   <supplier name> <invoice number> e.g. "MOTOVAC INV45678"

so the tokens this module looks for are the tokens Omni itself puts on the
bank. The claim-number and claim-with-invoice-suffix patterns are the ones
`taskboard/payment_duplicates.py` already proved against live data.

WHY THE BASIS CODE MUST SIT NEXT TO THE CLAIM NUMBER
----------------------------------------------------
A payment basis of "FOR" is a real code in Finance's vocabulary — and "for" is
also the commonest word in English narration text ("PAYMENT FOR REPAIRS").
Matching a bare "FOR" anywhere in a description would label ordinary prose as a
basis code. So a basis code counts ONLY when it immediately follows the claim
number, which is exactly the house format above. A code found anywhere else is
ignored — the line stays UNKNOWN, which is the safe answer (see below).

UNKNOWN IS NEVER DE-GROSSED
---------------------------
The bug B6 exists to stop is money being de-grossed for VAT when it should
not have been — about 11% of a 12-row sample. Every direction of doubt
therefore resolves to NOT de-grossing. A line is INVOICE basis only when there
is positive evidence of an invoice; absence of evidence is UNKNOWN, and every
basis other than INVOICE — UNKNOWN included — is left gross.

This module NEVER decides anything about money. It reports what the text says.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A Graphite claim number: G + 4-digit year + sequence. Same expression as
# taskboard/payment_duplicates.py:93, which was built against the live packs.
_CLAIM_RE = re.compile(r'\bG\d{4}\d{3,}\b', re.I)

# A claim number carrying an invoice-level suffix — 'G2026004567-4546' is ONE
# specific invoice of claim G2026004567 (Pako Kago, 2026-09-02). Read from raw
# text, before any normalisation, because the separator is the evidence.
_CLAIM_INVOICE_RE = re.compile(r'\b(G\d{4}\d{3,})[-/](\d+)\b', re.I)

# An invoice number as Finance writes it, with an explicit INV marker:
# 'INV45678', 'INV-45678', 'INVOICE 45678'. The capture must START with a
# digit: the first version was r'INV[- ]?(\w{2,20})' and it read the word
# INVOICE itself as an invoice number, 'OICE'.
_INVOICE_TOKEN_RE = re.compile(r'\bINV(?:OICE)?[-# ]?(\d[\w-]{1,19})\b', re.I)

# The house REPAIR narration puts the invoice number DIRECTLY after the claim
# number and nothing else: taskboard/narration_templates.py:194 writes
# 'ALPHA DIRECT G2026004782 3522'. That adjacency IS the evidence, and it is
# the commonest claims payment Omni raises.
#
# Bounded to 3-6 digits on purpose. There used to be a free-floating scan for
# ANY 5+ digit token anywhere on the line, and it was the most dangerous line
# in this module: FNB descriptions carry the bank's own numeric references and
# date stamps (fnb/statements.py), so 'ALPHA DIRECT G2026004512 20260715' read
# as invoice 20260715 -> INVOICE basis -> DE-GROSSED. That is precisely the
# over-de-grossing this report exists to stop, re-created by the parser that
# feeds it. A date stamp is 8 digits and no longer matches.
_CLAIM_ADJACENT_INVOICE_RE = re.compile(r'\bG\d{4}\d{3,}\s+(\d{3,6})\b', re.I)


class PaymentBasis:
    """What a claims payment was settled ON — the thing the VAT rule turns on.

    B6, verbatim: "De-gross VAT ONLY where the payment basis is INVOICE.
    Do NOT de-gross AOL, FOR or CIL payments."

    UNKNOWN is a first-class answer, not a failure. It means the line's text
    carried no evidence either way, and it is treated exactly like AOL/FOR/CIL
    for VAT purposes: left gross.
    """

    INVOICE = 'invoice'
    AOL = 'aol'
    FOR = 'for'
    CIL = 'cil'
    # The rest of the vocabulary Finance actually writes on the bank. These are
    # not inventions: they are PaymentNarrationType.CLAIM_LABEL in
    # taskboard/narration_templates.py, the module that WRITES the narration
    # this one reads. Without them a repair payment - the commonest claims
    # payment Omni raises - parsed as UNKNOWN and landed on the wrong side of
    # the supplier / individual-claimant split.
    REPAIR = 'repair'
    THIRD_PARTY = 'third_party'
    EX_GRATIA = 'ex_gratia'
    UNKNOWN = 'unknown'

    CHOICES = [
        (INVOICE, 'Supplier invoice'),
        (REPAIR, 'Repair'),
        (AOL, 'AOL'),
        (FOR, 'FOR'),
        (CIL, 'CIL'),
        (THIRD_PARTY, 'Third party'),
        (EX_GRATIA, 'Ex-gratia'),
        (UNKNOWN, 'Unknown - not evidenced on the line'),
    ]

    LABELS = dict(CHOICES)

    # The ONLY basis that may be de-grossed. Written as a set on purpose: the
    # next person to add a basis has to make a deliberate decision about VAT
    # rather than inherit one by accident.
    # REPAIR IS DELIBERATELY NOT IN HERE, AND THAT IS A QUESTION FOR FINANCE.
    # A repair settles a repairer's tax invoice, so an argument exists that it
    # should de-gross. The instruction names INVOICE and only INVOICE, and the
    # mistake being corrected is over-de-grossing, so doubt resolves to leaving
    # the money gross. REPAIR is reported as its own line so Finance can SEE
    # the amount involved and decide; adding it here later is a one-word change
    # and a VAT decision, not a code decision.
    DEGROSSED = frozenset({INVOICE})

    # The explicit codes Finance writes after a claim number. INVOICE is not
    # here: it is never spelled out on the bank, it is evidenced by an invoice
    # number being present.
    EXPLICIT_CODES = {
        'AOL': AOL, 'FOR': FOR, 'CIL': CIL,
        'REPAIR': REPAIR, 'THIRD PARTY': THIRD_PARTY, 'EX-GRATIA': EX_GRATIA,
    }

    # Bases that pay a PERSON, not a supplier invoice. A numeric token picked
    # up beside one of these is not an invoice number, and letting it through
    # would flip the supplier / individual-claimant split. REPAIR is NOT here:
    # a repair genuinely does settle an invoice, and its number is on the line.
    PAID_TO_A_PERSON = frozenset({AOL, FOR, CIL, THIRD_PARTY, EX_GRATIA})


# A basis code is only a basis code immediately after the claim number.
# The claim number may be either case - banks are inconsistent - but the CODE
# must be UPPERCASE. With re.I over the whole pattern, ordinary lowercase
# prose 'Payment G2026004512 for windscreen INV45678' read as basis FOR,
# which both mislabelled the payment AND discarded a real invoice number.
# Finance writes these codes in capitals; so does narration_templates.py.
_BASIS_AFTER_CLAIM_RE = re.compile(
    r'\b(?i:G)\d{4}\d{3,}(?:[-/]\d+)?\s+(AOL|FOR|CIL|REPAIR|THIRD PARTY|EX-GRATIA)\b'
)


@dataclass(frozen=True)
class LineReferences:
    """What one bank statement line says it was for."""

    claim_reference: str
    invoice_reference: str
    payment_basis: str

    @property
    def settled_against_invoice(self) -> bool:
        """True when this was a settlement of a SUPPLIER INVOICE, false when it
        went to an individual claimant. The B6 split, in one place."""
        return bool(self.invoice_reference)


EMPTY = LineReferences(claim_reference='', invoice_reference='',
                       payment_basis=PaymentBasis.UNKNOWN)


def _first_claim(text: str) -> str:
    m = _CLAIM_RE.search(text)
    return m.group(0).upper() if m else ''


def extract_line_references(description=None, reference=None) -> LineReferences:
    """Read a bank statement line's own text and report what it was for.

    `reference` is read FIRST and weighted highest: it is Omni's own 35-char
    endToEndId, written by the house templates, so it is the most reliable
    thing on the line. `description` is the bank's free text and is the
    fallback — but it is genuinely load-bearing, because in live data the real
    number is very often ONLY in the description (the whole reason
    `taskboard/payment_duplicates.py` harvests both).

    Returns blank strings and an UNKNOWN basis when the text carries no
    evidence. It never raises and never guesses.
    """
    ref_text = str(reference or '')
    desc_text = str(description or '')
    combined = f'{ref_text} {desc_text}'.strip()
    if not combined:
        return EMPTY

    claim = ''
    invoice = ''

    # 1. Claim-with-invoice-suffix is the richest single token — it names the
    #    claim AND which of that claim's invoices this row settled.
    m = _CLAIM_INVOICE_RE.search(combined)
    if m:
        claim, invoice = m.group(1).upper(), m.group(2)
    else:
        claim = _first_claim(combined)

    # 2. An explicit basis code, and ONLY where the house format puts it —
    #    directly after the claim number. See the module docstring for why a
    #    bare "FOR" anywhere in prose must not count.
    basis = PaymentBasis.UNKNOWN
    code = _BASIS_AFTER_CLAIM_RE.search(combined)
    if code:
        basis = PaymentBasis.EXPLICIT_CODES[code.group(1).upper()]

    # 3. An invoice number — but ONLY from positive evidence. There are exactly
    #    three things that count, and a bare number floating in the narration is
    #    not one of them: the bank puts its own numeric references and date
    #    stamps in that text, and reading one as an invoice number de-grosses a
    #    payment that never carried VAT. That is the mistake this whole report
    #    exists to stop; the parser must not be the thing that makes it.
    #
    #      (a) the claim-number suffix, handled at step 1;
    #      (b) an INV / INVOICE token;
    #      (c) digits immediately after the claim number — the house REPAIR
    #          format, 'ALPHA DIRECT G2026004782 3522'.
    #
    #    Not looked for at all when the line carries a code that says the money
    #    went to a person: 'G2026004512 AOL' is an AOL payment whatever else is
    #    written beside it.
    if not invoice and basis not in PaymentBasis.PAID_TO_A_PERSON:
        inv = _INVOICE_TOKEN_RE.search(combined)
        if inv:
            invoice = inv.group(1).upper()
        else:
            adj = _CLAIM_ADJACENT_INVOICE_RE.search(combined)
            if adj:
                invoice = adj.group(1)

    # 4. An invoice number present, and no code saying otherwise, is INVOICE
    #    basis — the only basis that may be de-grossed.
    if basis == PaymentBasis.UNKNOWN and invoice:
        basis = PaymentBasis.INVOICE

    # A payment to a person is not an invoice settlement: any numeric token
    # picked up alongside it is not an invoice number, and letting one through
    # would flip the supplier / individual-claimant split the wrong way.
    if basis in PaymentBasis.PAID_TO_A_PERSON:
        invoice = ''

    return LineReferences(
        claim_reference=claim[:50],
        invoice_reference=str(invoice)[:50],
        payment_basis=basis,
    )
