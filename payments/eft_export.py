"""
payments/eft_export.py

EFT batch file generator. Currently supports FNB BOL (FNB Online Banking
Bulk Payments) format — a fixed-width text file the bank ingests as a
batch payment run.

Layout (FNB BOL standard, simplified — VALIDATE AGAINST A REAL FNB SAMPLE
BEFORE GOING LIVE):

  Header  (1 row, type "01")
    01 | account_number(12) | run_date(YYYYMMDD) | batch_ref(20) | filler

  Detail (n rows, type "02") — one row per outbound payment
    02 | beneficiary_account_number(12) | branch_code(8) | amount_cents(15)
       | beneficiary_name(40) | reference(20) | filler

  Trailer (1 row, type "99")
    99 | record_count(5) | total_cents(15) | filler

Notes
- Amounts in cents (multiply BWP by 100, integer).
- Strings padded with spaces, numbers padded with leading zeros.
- All records 100 chars + CRLF.
- Only confirmed outbound payments with an ACTIVE vendor_bank_account
  and a numeric account number are included.

CFO sign-off required: this is generated against the *published* spec; a
real bank-supplied sample needs to be diffed before the first live run.
"""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from typing import Iterable

from .models import Payment


CRLF = '\r\n'
RECORD_LEN = 100  # chars per row


def _pad_left(value: str, width: int, fill: str = '0') -> str:
    s = (value or '')[:width]
    return s.rjust(width, fill)


def _pad_right(value: str, width: int, fill: str = ' ') -> str:
    s = (value or '')[:width]
    return s.ljust(width, fill)


def _digits_only(value: str) -> str:
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _amount_to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def _check_record(line: str) -> str:
    """Pad to RECORD_LEN — defensive in case caller miscounted."""
    if len(line) > RECORD_LEN:
        return line[:RECORD_LEN]
    return line.ljust(RECORD_LEN, ' ')


def build_fnb_bol(
    payments: Iterable[Payment],
    *,
    source_account_number: str,
    run_date,
    batch_ref: str,
) -> tuple[str, dict]:
    """
    Build the FNB BOL text payload for *payments*.

    Returns (text_payload, summary_dict). summary_dict carries:
        record_count   — number of detail rows actually written
        total_cents    — total amount in cents
        skipped        — list of (payment_number, reason) for rows excluded
    """
    out = StringIO()
    skipped: list[tuple[str, str]] = []
    total_cents = 0
    detail_rows: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────
    header = (
        '01'
        + _pad_left(_digits_only(source_account_number), 12)
        + run_date.strftime('%Y%m%d')
        + _pad_right(batch_ref, 20)
    )
    header = _check_record(header)
    out.write(header + CRLF)

    # ── Detail rows ──────────────────────────────────────────────────────
    for p in payments:
        if p.status != Payment.Status.CONFIRMED:
            skipped.append((p.payment_number, f'status={p.status}'))
            continue
        if p.payment_type != Payment.PaymentType.SENT:
            skipped.append((p.payment_number, 'not an outbound payment'))
            continue
        # Once-off payments carry their destination inline (payee_* fields)
        # — there is no vendor-bank register entry to read (Fable audit
        # 2026-07-07: they were silently skipped = GL said paid, bank file
        # never carried them).
        if getattr(p, 'is_once_off', False):
            acct = _digits_only(p.payee_account_number or '')
            branch = _digits_only(p.payee_branch_code or '')
            bene_name = (p.payee_name or p.contact.name)
            if not acct:
                skipped.append((p.payment_number, 'once-off payee account number missing'))
                continue
        else:
            if not p.vendor_bank_account_id:
                skipped.append((p.payment_number, 'no vendor bank account'))
                continue
            vba = p.vendor_bank_account
            if vba.status != vba.Status.ACTIVE:
                skipped.append((p.payment_number, f'vendor bank {vba.get_status_display()}'))
                continue
            acct = _digits_only(vba.account_number)
            branch = _digits_only(vba.branch_code or '')
            bene_name = p.contact.name
            if not acct:
                skipped.append((p.payment_number, 'beneficiary account number not numeric'))
                continue

        # The cash that actually leaves the bank, NOT the gross: withholding
        # tax and any early-settlement discount are credited away from the
        # bank in the GL and never leave us. `p.amount_bwp` instructed the
        # payee's tax as well (2026-09-20). Same derivation as the FNB API
        # rail, so the two files can never say different things.
        cents = _amount_to_cents(p.bank_instruction_amounts()[1])
        total_cents += cents

        line = (
            '02'
            + _pad_left(acct, 12)
            + _pad_left(branch, 8)
            + _pad_left(str(cents), 15)
            + _pad_right(bene_name, 40)
            + _pad_right(p.reference or p.payment_number, 20)
        )
        detail_rows.append(_check_record(line))

    for row in detail_rows:
        out.write(row + CRLF)

    # ── Trailer ───────────────────────────────────────────────────────────
    trailer = (
        '99'
        + _pad_left(str(len(detail_rows)), 5)
        + _pad_left(str(total_cents), 15)
    )
    out.write(_check_record(trailer) + CRLF)

    summary = {
        'record_count': len(detail_rows),
        'total_cents':  total_cents,
        'total_amount': str(Decimal(total_cents) / 100),
        'skipped':      [{'payment_number': pn, 'reason': r} for pn, r in skipped],
    }
    return out.getvalue(), summary
