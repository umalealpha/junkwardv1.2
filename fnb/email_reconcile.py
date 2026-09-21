"""fnb/email_reconcile.py — read FNB's payment-result emails and decide which open
Omni payment requests they prove PAID (CFO 2026-08-23).

Why this exists
---------------
Omni's API statement feed for the Claims account has been returning empty for days,
so payments made there never reconcile and the CFO has to compare FNB against Omni
by hand. But FNB *also* emails a per-payment confirmation to the CFO mailbox for
every OnceOff payment, e.g.:

    FNB:-) The OnceOff Payment Unicoin-AD Transportation2108 to the total value of
    BWP2800.00 has been processed and is now in a status of Fully Processed.

"Fully Processed" is FNB's only true "paid" status (Authorised / Submitted are NOT
paid). This module turns those emails into (reference, amount, paid?) facts, and
matches them to open payment requests — so the queue can close itself.

Money-safety rules (mirroring taskboard/fnb_reconcile.py):
  * A request is only ever matched on EXACT amount AND a reference overlap — never
    on amount alone (two payments can share an amount; the classic double-pay).
  * Only status == "Fully Processed" counts as paid. Anything else is ignored.
  * A match must be UNAMBIGUOUS — exactly one open request fits. 0 or >1 → no action,
    left for a human. False "keep" is harmless; false "close" is not.

Pure functions, no Django imports, so they are trivially unit-testable.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# The body FNB sends. The reference is whatever the payer typed as the beneficiary
# reference (name + invoice), which is echoed verbatim between "OnceOff Payment "
# and " to the total value of". Amount is BWP<n> with two decimals.
_RESULT_RE = re.compile(
    r'OnceOff Payment\s+(?P<ref>.+?)\s+to the total value of\s+'
    r'BWP\s*(?P<amt>[\d,]+\.\d{2})\b.*?'
    r'status of\s+(?P<status>[A-Za-z][A-Za-z ]*?)\s*\.',
    re.I | re.S,
)

_PAID_STATUS = 'fully processed'

# Non-word noise to drop when comparing two references so "Unicoin-AD
# Transportation2108" and "UNICOIN-AD TRANSPORTATION 2108" compare equal.
_NON_ALNUM = re.compile(r'[^a-z0-9]+')


def _to_amount(raw) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).replace(',', '').strip()
    if not s:
        return None
    try:
        return Decimal(s).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def _norm(text: str) -> str:
    """Lowercase, strip everything but letters/digits — for reference comparison."""
    return _NON_ALNUM.sub('', (text or '').lower())


def parse_result_email(body: str) -> dict | None:
    """Turn one FNB result-email body into {ref, amount, status, paid} or None.

    None means the email is not a recognisable OnceOff payment result (a different
    FNB email, a bounce, etc.) — the caller skips it rather than guessing.
    """
    if not body:
        return None
    m = _RESULT_RE.search(body)
    if not m:
        return None
    amount = _to_amount(m.group('amt'))
    if amount is None:
        return None
    status = ' '.join(m.group('status').split()).strip().lower()
    return {
        'ref':    ' '.join(m.group('ref').split()).strip(),
        'amount': amount,
        'status': status,
        'paid':   status == _PAID_STATUS,
    }


def _reference_overlaps(email_ref: str, request: dict) -> bool:
    """True if the FNB email's reference clearly names this request.

    Compared on alphanumerics only, both directions, against the fields that carry
    what we told the bank (our reference, the narration, the payee) plus the
    per-line refs. Requires a token of real length so a stray "2026" can't match.
    """
    e = _norm(email_ref)
    if not e:
        return False
    candidates = [
        request.get('bank_our_reference'),
        request.get('bank_narration'),
        request.get('payee'),
        request.get('subject'),
    ]
    for ln in request.get('line_items') or []:
        if isinstance(ln, dict):
            candidates.append(ln.get('ref'))
            candidates.append(ln.get('description'))
    for c in candidates:
        n = _norm(c or '')
        # Guard against trivial overlaps: whichever string is the substring must
        # itself be at least 6 chars, so a short ref ("rent") can't match either way.
        if (len(n) >= 6 and n in e) or (len(e) >= 6 and e in n):
            return True
    return False


def _batch_matches(email_ref: str, request: dict) -> bool:
    """True if the FNB email's reference IS the EFT batch id Omni loaded this
    payment under (PaymentRequest.fnb_batch.idempotency_key). That id is
    globally unique, so it is the strongest possible match for an
    omni-submitted payment — and the only one available when the payee
    reference was never stored on the request.

    This used to be a >=12-char substring check ('bk in email_ref'), on the
    assumption the id looked like 'ALPHA-EFT-20260821-09da76bc3db645f8'.
    Production's real idempotency_key is short-vendor-name + batch-number +
    entity-code, e.g. 'GRAND RE 177 (O)' (normalises to 11 chars) and
    'SCANIA 195 (O)' (10 chars) — both the CFO's actual live payments, both
    an EXACT match against FNB's own confirmation email, both silently
    refused by the old >=12 guard and left sitting open forever (proved on
    production 2026-09-14: three PENDING_CFO requests each had an
    identical-string email match this function was rejecting on length
    alone).

    Fixed as an EQUALITY check, not a shorter substring guard: the docstring
    itself says the email reference IS the batch id, and every real FNB
    confirmation body echoes the exact submitted reference back — there is
    no case in this codebase of a genuine substring (a shorter batch_key
    embedded in a longer, different email reference). A substring check with
    a merely-lowered length floor would let an unrelated LONGER reference
    that happens to contain a short key false-match (panel review,
    2026-09-14); equality cannot."""
    bk = _norm(request.get('batch_key') or '')
    if not bk:
        return False
    return bk == _norm(email_ref)


def match_paid_email(parsed: dict, requests: list[dict]) -> dict:
    """Given one PAID parsed email and the matchable requests, decide what to close.

    `requests`: [{id, ref, total, bank_our_reference, bank_narration, payee,
                  subject, line_items, is_open}] — PENDING_CFO requests PLUS
    recently-terminal (paid/cancelled) ones. `is_open` marks the closeable ones
    (defaults True when absent, for pure unit tests).

    Returns {action, request_id?, reason?, candidates}:
      * action='close'      exactly one OPEN request matches on amount + reference,
                            and no already-terminal request matches.
      * action='ambiguous'  more than one OPEN request matches — a human decides.
      * action='none'       nothing matches, OR a terminal request already matches
                            (already reconciled — do not touch an open sibling).
    """
    if not parsed or not parsed.get('paid'):
        return {'action': 'none', 'candidates': []}
    amount = parsed['amount']
    matches = [
        r for r in requests
        if _to_amount(r.get('total')) == amount
        and (_reference_overlaps(parsed['ref'], r)
             or _batch_matches(parsed['ref'], r))
    ]
    # This email's payment is already reconciled if a terminal request matches it.
    # Stop here — never fall through to an open same-vendor/same-amount sibling.
    # False "keep" is harmless; false "close" pays/closes the wrong thing.
    if any(not r.get('is_open', True) for r in matches):
        return {'action': 'none', 'candidates': [r.get('ref') for r in matches]}
    open_hits = [r for r in matches if r.get('is_open', True)]
    if len(open_hits) == 1:
        r = open_hits[0]
        return {
            'action': 'close',
            'request_id': r.get('id'),
            'request_ref': r.get('ref'),
            'reason': (f"Paid via FNB - confirmed Fully Processed "
                       f"(FNB email: {parsed['ref']}, BWP{amount:,.2f})."),
            'candidates': [r.get('ref') for r in open_hits],
        }
    if len(open_hits) > 1:
        return {'action': 'ambiguous', 'candidates': [r.get('ref') for r in open_hits]}
    return {'action': 'none', 'candidates': []}


def catchup_matches(request: dict, paid_emails: list[dict]) -> dict:
    """Human-supervised catch-up matcher (CFO 2026-08-24).

    match_paid_email() above is the AUTOMATIC matcher: it only ever closes on an
    exact amount PLUS a reference/batch overlap, because a machine must never pay
    on amount alone. But that leaves stuck the older requests that were paid
    straight in FNB (never loaded through Omni), so there is no Omni reference for
    the bank email to overlap with — they can never auto-close.

    This matcher exists for a SCREEN where the CFO confirms each row by eye. It
    therefore also surfaces EXACT-AMOUNT-ONLY candidates, clearly graded so the UI
    can colour the risk. It NEVER closes anything itself — the endpoint only marks
    a request paid on an explicit human click.

    `paid_emails`: parsed FNB "Fully Processed" emails (each {ref, amount, ...},
    optionally 'date'). Returns {confidence, matches}:
      * 'confident'  — a reference/batch overlap, i.e. the auto-matcher agrees.
      * 'review'     — exactly one email matches on amount alone; eyeball the payee.
      * 'ambiguous'  — more than one email at this amount; the CFO picks which.
      * 'none'       — no paid email at this amount (still genuinely waiting).
    """
    amt = _to_amount(request.get('total'))
    if amt is None:
        return {'confidence': 'none', 'matches': []}
    at_amount = [e for e in paid_emails if e.get('amount') == amt]
    if not at_amount:
        return {'confidence': 'none', 'matches': []}
    strict = [e for e in at_amount
              if _reference_overlaps(e['ref'], request) or _batch_matches(e['ref'], request)]
    if strict:
        return {'confidence': 'confident', 'matches': strict}
    if len(at_amount) == 1:
        return {'confidence': 'review', 'matches': at_amount}
    return {'confidence': 'ambiguous', 'matches': at_amount}


def open_reason(*, batch_status='', failure_reason='', has_batch=False,
                is_exception=False, email_confidence='none',
                batch_statuses=()) -> tuple[str, str]:
    """Plain-English reason an OPEN payment request is still open, + a colour tone.

    Built for the CFO (a layman): it answers "why is this still sitting here?" so an
    unpaid queue never looks like a broken auto-closer (CFO 2026-09-05). Pure and
    unit-testable — no Django, no network.

    Inputs are facts already on the record:
      * batch_status     — the FNB batch status ('submitted'/'settled'/'failed'/
                           'acknowledged'/'unknown'/'pending'/'' when no batch).
      * failure_reason   — FNB's rejection text, when failed.
      * has_batch        — was this ever loaded to FNB through Omni?
      * is_exception     — is the request held in exception review?
      * email_confidence — catchup_matches() verdict for this request
                           ('confident'/'review'/'ambiguous'/'none'); pass 'none'
                           when the bank emails were not consulted, or 'unavailable'
                           when the bank-email read failed this load (so a "waiting"
                           row admits it could not check for an existing payment).

    Returns (text, tone) where tone is one of:
      'reject' (red) · 'check' (amber, needs your eye) · 'paid' (green, closing) ·
      'exception' (amber) · 'wait' (grey, just waiting on your FNB sign-off).
    """
    # 0. PART-PAID (CFO master M4, 18-Sep-2026). A request paid line by line
    #    has one FNB batch PER LINE, and batch_status above is only the one the
    #    request's single FK happens to hold. Settled + rejected together must
    #    never read as plain "Rejected — reload" (that invites re-sending the
    #    lines already paid) nor as plain "Paid" (that hides the rejected ones).
    #    'cancelled' never left Omni, so it is not counted.
    sts = [s for s in (batch_statuses or ()) if s and s != 'cancelled']
    if len(sts) > 1:
        paid = sts.count('settled')
        dead = sum(1 for s in sts if s in ('failed', 'unknown'))
        if paid and dead:
            return (f'Part-paid: {paid} of {len(sts)} bank lines settled, {dead} '
                    'rejected or unconfirmed. Do NOT reload the paid lines - fix '
                    'and reload only the failed ones.', 'check')
        if paid and paid < len(sts):
            return (f'Part-paid: {paid} of {len(sts)} bank lines settled; the rest '
                    'are still with FNB.', 'wait')
        if dead and not paid and dead < len(sts):
            return (f'{dead} of {len(sts)} bank lines rejected or unconfirmed by FNB; '
                    'the rest are still with FNB. Fix and reload only the rejected '
                    'ones.', 'reject')
    # 1. FNB REJECTED it — top priority: it will NEVER auto-close on its own.
    if batch_status == 'failed':
        why = 'Rejected by FNB'
        fr = ' '.join((failure_reason or '').split())
        if fr:
            why += f' - {fr[:70]}'
        return (f'{why}. Fix the bank/branch details and reload.', 'reject')
    # 2. FNB gave no clean answer — the money MAY have left; a human must verify.
    if batch_status == 'unknown':
        return ('FNB gave no clear result - the money may have left. '
                'Verify with FNB before re-sending.', 'check')
    # 3. GREEN "paid" comes ONLY from the bank API marking the batch settled (funds
    #    debited). NEVER from a matched email: catchup_matches has no terminal-sibling
    #    guard, so last month's "Fully Processed" email for a recurring same-amount,
    #    same-payee payment would falsely paint THIS month's open request green
    #    forever (Fable F2, 2026-09-06).
    if batch_status == 'settled':
        return ('Paid - closing shortly (bank confirmed).', 'paid')
    # 4. A "Fully Processed" email matches this amount (by reference/batch id OR
    #    amount-only). It may be a real prior payment OR a same-amount lookalike -
    #    so never assert paid; tell the CFO to check before authorising again.
    if email_confidence in ('confident', 'review', 'ambiguous'):
        return ('A bank email shows this exact amount PAID - check before '
                'authorising again (open "Check my email").', 'check')
    # 5. Held for exception review (committee / flagged) - not the closer's job.
    if is_exception:
        return ('Held for exception review.', 'exception')
    # 6. Loaded to FNB, sitting in the bank waiting for YOUR authorisation. If the
    #    bank-email check could not run this load ('unavailable'), say so plainly -
    #    a silent fall-back to "just waiting" could hide an already-paid payment
    #    (Fable F1, 2026-09-06).
    if batch_status in ('submitted', 'acknowledged', 'pending'):
        base = 'Waiting for your authorisation in the FNB app.'
        if email_confidence == 'unavailable':
            base += (' Bank-email check unavailable right now - confirm it is not '
                     'already paid.')
        return (base, 'wait')
    # 7. No FNB batch at all - never loaded through Omni (paid by hand, or not sent).
    if not has_batch:
        return ('Not loaded to FNB (paid by hand, or not sent yet) - check.', 'check')
    base = 'Waiting.'
    if email_confidence == 'unavailable':
        base += (' Bank-email check unavailable right now - confirm it is not '
                 'already paid.')
    return (base, 'wait')
