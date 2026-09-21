"""genric/invoice.py — the GENRIC-1-0XX sequence.

The build prompt says: "Invoice number GENRIC-1-0XX; sequential; July was 024 —
confirm next." Confirm, not assume. A reinsurance invoice number that collides
with one already issued, or skips one, is a query from GENRIC's side and a
reconciliation exercise on ours.

So: the next number is DERIVED from the last pack run and then offered for
confirmation. Finance may pass an explicit number, which always wins. On a
system with no run history and no explicit number the pack refuses to invent
one — it produces everything else and raises the number as an open item.
"""
from __future__ import annotations

import re
from typing import Optional

from . import constants as K

_RE = re.compile(rf'^{re.escape(K.INVOICE_PREFIX)}(\d+)$')


class InvoiceNumberUnconfirmed(RuntimeError):
    """No invoice number could be derived and none was supplied."""


def parse(number: str) -> Optional[int]:
    m = _RE.match((number or '').strip().upper())
    return int(m.group(1)) if m else None


def format_number(sequence: int) -> str:
    return f'{K.INVOICE_PREFIX}{sequence:0{K.INVOICE_NUMBER_WIDTH}d}'


def last_issued() -> Optional[int]:
    """Highest sequence already issued by a completed pack run.

    Ordered by the parsed integer, NOT by the string: 'GENRIC-1-100' sorts
    before 'GENRIC-1-024' as text, so a text max would hand back a number that
    has already been used the moment the sequence passes 099.
    """
    from .models import GenricPackRun
    numbers = [
        parse(n) for n in GenricPackRun.objects
        .exclude(invoice_number='')
        .values_list('invoice_number', flat=True)
    ]
    numbers = [n for n in numbers if n is not None]
    return max(numbers) if numbers else None


def next_invoice_number(explicit: Optional[str] = None) -> tuple[str, bool]:
    """Return (invoice_number, needs_confirmation).

    ``needs_confirmation`` is True whenever the number was derived rather than
    supplied — it goes on the Master report's Open Items so Finance ticks it
    before the invoice leaves.
    """
    if explicit:
        parsed = parse(explicit)
        if parsed is None:
            raise InvoiceNumberUnconfirmed(
                f'{explicit!r} is not a {K.INVOICE_PREFIX}NNN invoice number.'
            )
        return format_number(parsed), False

    last = last_issued()
    if last is None:
        raise InvoiceNumberUnconfirmed(
            'No GENRIC invoice number has been issued from Omni yet, so the '
            'next one in the sequence cannot be derived. July 2026 was '
            f'{format_number(24)} — supply the next number explicitly for this '
            'first run and every run after it will follow on automatically.'
        )
    return format_number(last + 1), True
