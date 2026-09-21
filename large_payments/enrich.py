"""Pull the claim facts for a large-payment request out of Graphite.

The CFO asked for "10% done, 20% done, 60% done" rather than a frozen box,
because this genuinely takes time. So the claims are fetched ONE AT A TIME inside
a single connection and the percentage is written to the database after each —
the number on his screen is the real position, not an animation. A fake progress
bar is a lie told in a payment screen, and the whole point of this module is that
nothing on it is guessed.

READ ONLY, three ways over: integrations.graphite_ro refuses a non-replica host,
refuses any statement that is not a SELECT, and sets the session read-only. This
module adds no fourth way in — it calls that one.

SCHEMA NOTES, learned the hard way and verified 1-Sep and 4-Sep-2026:
  * the customer table is `customer` (singular) with camelCase `firstName` /
    `lastName`. Getting the name or the casing wrong returns NULL rather than an
    error, so the mistake shows up as a blank insured name on a payment document
    and nowhere else.
  * `claims.incident_description` is routinely NULL on real claims; the loss
    narrative lives in `new_claims.description_of_loss`.
  * the insured COMPANY is not `policies.business_name` (null on COMG policies) —
    it is `customer.company_id` -> `companies.name`. `customer.firstName/lastName`
    is the contact person, not the insured.
  * `new_claims.reserve_amount` / `paid_amount` are often NULL or 0 on claims that
    are genuinely being paid. A zero reserve does NOT mean "no money involved",
    so it is surfaced as a flag for a human, never used to judge the payment.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from integrations import graphite_ro

from .models import LargePaymentLine, LargePaymentRequest

log = logging.getLogger(__name__)

#: One claim, everything the authorisation document needs, in one round trip.
#: LEFT JOINs throughout: a claim with no policy, no customer or no new_claims row
#: must still come back as a row with blanks, so "found but thin" is
#: distinguishable from "not in Graphite at all".
_CLAIM_SQL = """
SELECT  c.claim_number                AS claim_number,
        c.status                      AS claim_status,
        c.claim_sub_status            AS claim_sub_status,
        p.policyNumber                AS policy_number,
        p.business_name               AS business_name,
        cu.firstName                  AS first_name,
        cu.lastName                   AS last_name,
        co.name                       AS company_name,
        nc.date_of_loss               AS date_of_loss,
        nc.description_of_loss        AS loss_description,
        nc.reserve_amount             AS reserve_amount,
        nc.paid_amount                AS paid_amount
FROM        claims      c
LEFT JOIN   policies    p  ON p.id  = c.policy_id
LEFT JOIN   customer    cu ON cu.id = p.customer_id
LEFT JOIN   companies   co ON co.id = cu.company_id
LEFT JOIN   new_claims  nc ON nc.claim_number = c.claim_number
WHERE       c.claim_number = %s
ORDER BY    nc.id DESC
LIMIT 1
"""

#: Names that mean "nobody filled this in". A payment document must never carry
#: one as if it were a real insured (see the "Tbc Tbc" case, 1-Sep-2026).
_PLACEHOLDER_NAMES = {'', 'tbc', 'tbc tbc', 'n/a', 'na', 'unknown', 'test', '-', '.'}

#: Claim states where paying is at least worth a second look before the CFO signs.
_ODD_STATES = ('closed', 'rejected', 'repudiated', 'declined', 'cancelled', 'void')


def _decimal(raw):
    if raw in (None, ''):
        return None
    try:
        return Decimal(str(raw)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def _insured_name(row: dict) -> str:
    """The insured, preferring the company over the contact person."""
    company = (row.get('company_name') or row.get('business_name') or '').strip()
    if company:
        return company
    person = ' '.join(p for p in ((row.get('first_name') or '').strip(),
                                  (row.get('last_name') or '').strip()) if p)
    return person.strip()


def _flags_for(line: LargePaymentLine, row: dict | None) -> list[dict]:
    """What a human must look at. Never silently swallowed, never auto-resolved."""
    out: list[dict] = []

    if not line.claim_number:
        out.append({'code': 'no_claim_number',
                    'message': 'This payment carries no Graphite claim number, so '
                               'no claim detail could be looked up. Check it '
                               'belongs on a claims authorisation at all.'})
        return out

    if row is None:
        out.append({'code': 'not_in_graphite',
                    'message': f'Claim {line.claim_number} was not found in '
                               'Graphite. Do not put it to the CEO until Claims '
                               'confirm the number.'})
        return out

    name = _insured_name(row)
    if name.strip().lower() in _PLACEHOLDER_NAMES:
        out.append({'code': 'no_insured_name',
                    'message': 'Graphite has no usable insured name on this claim '
                               '(it is blank or a placeholder). Never type a name '
                               'in to fill the gap — ask Claims.'})

    state = f"{row.get('claim_status') or ''} {row.get('claim_sub_status') or ''}".lower()
    if any(word in state for word in _ODD_STATES):
        out.append({'code': 'odd_claim_state',
                    'message': f"The claim reads '{(row.get('claim_status') or '').strip()}"
                               f" / {(row.get('claim_sub_status') or '').strip()}'"
                               ' in Graphite while a payment is going out on it.'})

    reserve = _decimal(row.get('reserve_amount'))
    if reserve is None or reserve == 0:
        out.append({'code': 'no_reserve',
                    'message': 'Graphite shows no reserve on this claim. That is '
                               'often just missing data rather than a problem, but '
                               'it means the amount cannot be checked against a '
                               'reserve here.'})
    elif line.amount > reserve:
        out.append({'code': 'over_reserve',
                    'message': f'The payment ({line.amount}) is larger than the '
                               f'reserve Graphite holds ({reserve}).'})

    return out


def _duplicate_flags(lines: list[LargePaymentLine]) -> None:
    """Two lines on one request that look like the same payment twice.

    FOUND ON THE LIVE LIST, 2026-09-11, minutes after Piece 1 went live: claim
    G2026004923 appeared TWICE at exactly BWP 231,840.00 and this check said
    NOTHING. It only compared PAYEE + amount, and the payee field is EMPTY on
    those records — so the one control whose whole job is catching a repeat was
    defeated by a blank field. A duplicate check keyed on the one column the data
    does not reliably carry is a control in name only.

    It now matches on CLAIM NUMBER + amount as well, which is the stronger test:
    the claim number is the thing a claims payment is actually identified by, and
    two payments of the same amount against the same claim on one request is
    exactly the case a human must look at. Payee + amount is KEPT, not replaced —
    it still catches a repeat to one supplier across two different claims.

    The one-thebe trick (88,099.65 against 88,099.64) defeats an exact match, so
    amounts within one thebe count as the same amount.

    This BLOCKS NOTHING. It puts it in front of a person, because a legitimate
    reason for two identical payments does exist (a part-payment, a re-issue) and
    the module has no way to tell.
    """

    def _same_amount(a, b) -> bool:
        return abs(a.amount - b.amount) <= Decimal('0.01')

    def _add(ln, msg) -> None:
        if not any(f.get('code') == 'possible_duplicate' for f in ln.flags):
            ln.flags = list(ln.flags) + [{'code': 'possible_duplicate',
                                          'message': msg}]

    for i, a in enumerate(lines):
        for b in lines[i + 1:]:
            if not _same_amount(a, b):
                continue

            claim_a = (a.claim_number or '').strip().upper()
            claim_b = (b.claim_number or '').strip().upper()
            payee_a = (a.payee or '').strip().lower()
            payee_b = (b.payee or '').strip().lower()

            # Claim number first — the reliable identifier.
            if claim_a and claim_a == claim_b:
                msg = (f'Claim {claim_a} appears TWICE on this request for the '
                       f'same amount ({a.amount} and {b.amount}). Confirm these '
                       'are two genuinely different payments before approving.')
            # Then payee, for a repeat to one supplier across different claims.
            elif payee_a and payee_a == payee_b:
                msg = (f'{a.payee} appears twice on this request for almost the '
                       f'same amount ({a.amount} and {b.amount}). Confirm these '
                       'are two genuinely different payments.')
            # Neither identifier available: say so rather than stay silent. Two
            # identical amounts with nothing to tell them apart is the WORST
            # case to hide, not a reason to skip the check.
            elif not claim_a and not payee_a and not claim_b and not payee_b:
                msg = (f'Two payments of the same amount ({a.amount}) sit on this '
                       'request with no claim number and no payee to tell them '
                       'apart. Check with Finance which is which.')
            else:
                continue

            _add(a, msg)
            _add(b, msg)


def _set_progress(req: LargePaymentRequest, pct: int, note: str) -> None:
    req.progress_pct = max(0, min(100, int(pct)))
    req.progress_note = note[:160]
    req.save(update_fields=['progress_pct', 'progress_note', 'updated_at'])


def enrich(request_id) -> None:
    """Fill in the claim detail for one request, then hand it to the CFO.

    Safe to call twice: it re-reads its own rows and overwrites them. Any failure
    leaves the request in ENRICH_FAILED with the reason on the record — never
    half-filled and never silently marked ready.
    """
    try:
        req = LargePaymentRequest.objects.get(pk=request_id)
    except LargePaymentRequest.DoesNotExist:
        log.warning('large_payments: request %s vanished before enrichment', request_id)
        return

    lines = list(req.lines.select_related('payment_request'))
    if not lines:
        _set_progress(req, 100, 'Nothing to collect.')
        _finish(req)
        return

    if not graphite_ro.is_configured():
        req.status = LargePaymentRequest.Status.ENRICH_FAILED
        req.enrich_error = ('Omni cannot reach the Graphite read-only replica, so '
                            'no claim details could be collected. Nothing has been '
                            'sent anywhere. Ask IT to check the Graphite read '
                            'connection, then press Try again.')
        req.progress_note = 'Could not reach Graphite.'
        req.save(update_fields=['status', 'enrich_error', 'progress_note', 'updated_at'])
        return

    _set_progress(req, 5, f'Connecting to Graphite for {len(lines)} claim(s)…')

    try:
        with graphite_ro.connection() as cx:
            for i, line in enumerate(lines, start=1):
                row = None
                if line.claim_number:
                    try:
                        graphite_ro.assert_read_only(_CLAIM_SQL)
                        with cx.cursor() as cur:
                            cur.execute(_CLAIM_SQL, [line.claim_number])
                            row = cur.fetchone()
                    except Exception as exc:            # noqa: BLE001
                        log.exception('large_payments: claim %s lookup failed',
                                      line.claim_number)
                        line.enrich_status = LargePaymentLine.EnrichStatus.ERROR
                        line.flags = [{'code': 'lookup_failed',
                                       'message': f'The Graphite lookup for '
                                                  f'{line.claim_number} failed: {exc}'}]
                        line.save()
                        _set_progress(req, 5 + int(90 * i / len(lines)),
                                      f'{i} of {len(lines)} claims — one failed.')
                        continue

                if not line.claim_number:
                    line.enrich_status = LargePaymentLine.EnrichStatus.NO_CLAIM
                elif row is None:
                    line.enrich_status = LargePaymentLine.EnrichStatus.NOT_FOUND
                else:
                    line.enrich_status = LargePaymentLine.EnrichStatus.OK
                    line.insured_name     = (_insured_name(row) or '')[:200]
                    line.policy_number    = (row.get('policy_number') or '')[:60]
                    line.claim_status     = (row.get('claim_status') or '')[:60]
                    line.claim_sub_status = (row.get('claim_sub_status') or '')[:60]
                    line.date_of_loss     = row.get('date_of_loss') or None
                    line.loss_description = row.get('loss_description') or ''
                    line.reserve_amount   = _decimal(row.get('reserve_amount'))
                    line.paid_amount      = _decimal(row.get('paid_amount'))

                line.flags = _flags_for(line, row)
                line.save()
                _set_progress(req, 5 + int(90 * i / len(lines)),
                              f'{i} of {len(lines)} claims collected.')
    except Exception as exc:                             # noqa: BLE001
        log.exception('large_payments: enrichment failed for %s', req.ref)
        req.status = LargePaymentRequest.Status.ENRICH_FAILED
        req.enrich_error = (f'Collecting the claim details stopped: {exc}. Nothing '
                            'has been sent anywhere and no payment was changed. '
                            'Press Try again.')
        req.progress_note = 'Stopped before finishing.'
        req.save(update_fields=['status', 'enrich_error', 'progress_note', 'updated_at'])
        return

    # EVERY lookup failing is a broken connection wearing the costume of a
    # finished run (Fable 5.1, 2026-09-11). Without this the request went to the
    # CFO as "ready for your authorisation" with every line blank — the exact
    # "never silently marked ready" this module's docstring promises not to do.
    # One failure among many is a flag; all of them is an outage, and an outage
    # is retryable rather than approvable.
    with_claims = [ln for ln in lines if ln.claim_number]
    if with_claims and all(ln.enrich_status == LargePaymentLine.EnrichStatus.ERROR
                           for ln in with_claims):
        req.status = LargePaymentRequest.Status.ENRICH_FAILED
        req.enrich_error = ('Every claim lookup failed, so no claim details were '
                            'collected at all. This is almost always the Graphite '
                            'connection rather than the claims. Nothing has been '
                            'sent anywhere. Press Try again.')
        req.progress_note = 'Every lookup failed.'
        req.save(update_fields=['status', 'enrich_error', 'progress_note', 'updated_at'])
        return

    _duplicate_flags(lines)
    for ln in lines:
        ln.save(update_fields=['flags'])

    _set_progress(req, 100, 'All claim details collected.')
    _finish(req)


def _finish(req: LargePaymentRequest) -> None:
    """Move to PENDING_CFO and raise the CFO's task."""
    req.status = LargePaymentRequest.Status.PENDING_CFO
    req.enrich_error = ''
    req.save(update_fields=['status', 'enrich_error', 'updated_at'])

    from .tasks import raise_cfo_task
    raise_cfo_task(req)


def enrich_in_background(request_id) -> None:
    """Run enrich() off the request thread so the button returns immediately.

    A thread, not a queue: this repo has no worker for taskboard work, the job is
    seconds long, and the progress it writes is in the database rather than in
    memory — so if the process is recycled mid-run the request is left visibly
    stuck at its last percentage with a Try again button, rather than appearing
    finished. That is the failure mode worth having.
    """
    import threading
    from django.db import connection as django_cx

    def _run():
        try:
            enrich(request_id)
        finally:
            django_cx.close()

    threading.Thread(target=_run, daemon=True,
                     name=f'large-payment-enrich-{request_id}').start()
