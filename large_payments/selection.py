"""Which payments belong in a large-payment authorisation request.

READ ONLY. Nothing in this file writes to taskboard.PaymentRequest or to any
payment. It answers one question — "which claim payments over the cut-off have
reached FNB and are not already on a request?" — and it answers it from the rows
Omni's own gated payment path produced.
"""
from __future__ import annotations

import re
from decimal import Decimal

from django.db.models import Q

from taskboard.models import PaymentRequest

from .models import LargePaymentLine, LargePaymentRequest, default_threshold

#: The claim-number shapes Omni already trusts. Imported rather than re-written:
#: taskboard.fnb_reconcile has matched payments across Omni and FNB on this exact
#: pattern since August, and a second, slightly different regex for the same
#: concept is how the two halves of a system drift apart.
from taskboard.fnb_reconcile import _TOKEN_RE as CLAIM_TOKEN_RE

#: A claim token sitting at the FRONT of a line description, which is how roughly
#: half of the live lines carry it — "G2026004287 CARFIL SERVICES" with the
#: claim_number field left empty (verified on live data 2026-08-09; see
#: taskboard/payment_views.py line 1339).
_CLAIM_IN_DESC = re.compile(r'^\s*((?:G20\d{6,}|(?:DOMG|DOMD|MIS|COMG|COMD)\d{6,}))\b', re.I)


def claim_number_for(pr: PaymentRequest) -> str:
    """The Graphite claim number on a payment request, or ''.

    Looks in the three places a claim number actually lives, in the order they
    are trustworthy: the line's own claim_number field, the front of the line
    description, then the request subject. It never invents one.
    """
    for ln in (pr.line_items or []):
        if not isinstance(ln, dict):
            continue
        claim = str(ln.get('claim_number') or ln.get('claim_no')
                    or ln.get('claim') or '').strip().upper()
        if claim:
            return claim
        desc = str(ln.get('description') or ln.get('ref') or '').strip()
        m = _CLAIM_IN_DESC.match(desc)
        if m:
            return m.group(1).upper()
    m = CLAIM_TOKEN_RE.search(pr.subject or '')
    return m.group(0).upper() if m else ''


def payments_already_requested() -> set:
    """Payment-request ids sitting on a live large-payment request.

    A rejected or cancelled request releases its payments — they can be picked up
    again on a new one. An approved or pending one holds them, so the same
    payment can never be put to the CFO twice.
    """
    return set(
        LargePaymentLine.objects
        .exclude(request__status__in=LargePaymentRequest.RELEASING_STATUSES)
        .values_list('payment_request_id', flat=True)
    )


def candidates(threshold: Decimal | None = None):
    """The claim payments eligible for a request, largest first.

    Eligible means all four of:
      * category is CLAIM — this button is for claim payments only;
      * it actually reached FNB (fnb_loaded_at is set). The CFO's answer on
        2026-09-11 was to keep the request AFTER the bank load, as the manual
        process does today, so a payment that has not been loaded has nothing to
        authorise yet;
      * it is not already held on another large-payment request;
      * it was not rejected or cancelled in Omni.

    The threshold is NOT applied here. Everything eligible is returned and the
    caller marks what is over the cut-off, because the CFO asked to be able to
    add a smaller payment by hand — hiding the rest would make that impossible.
    """
    cut = default_threshold() if threshold is None else threshold
    held = payments_already_requested()

    qs = (PaymentRequest.objects
          .filter(category=PaymentRequest.Category.CLAIM)
          .filter(fnb_loaded_at__isnull=False)
          .exclude(status__in=('rejected', 'cancelled', 'draft'))
          .exclude(id__in=held)
          .order_by('-total'))

    out = []
    for pr in qs:
        out.append({
            'payment_request_id': str(pr.id),
            'ref':          pr.ref,
            'subject':      pr.subject,
            'payee':        pr.payee,
            'amount':       str(pr.total),
            'claim_number': claim_number_for(pr),
            'status':       pr.status,
            'fnb_loaded_at': pr.fnb_loaded_at.isoformat() if pr.fnb_loaded_at else None,
            'over_threshold': pr.total >= cut,
        })
    return out, cut
