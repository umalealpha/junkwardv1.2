"""
integrations/claim_insight.py — the deterministic "Claim insight" for a claim
number on a payment request (CFO 2026-08-31, Claim-Description-Spec).

When a claims payment request quotes a Graphite claim number, Finance needs the
claim's facts at a glance instead of opening Graphite claim-by-claim. This reads
the GraphiteClaim mirror (kept in sync by pull_graphite_claims) and returns, for
one claim number:

  * the FACTS (customer, policy, product, loss, reserve/paid/balance, status …),
  * ``facts_text`` — a compact one-paragraph description used to auto-fill the
    payment line's description,
  * HARD FLAGS computed BY RULE (never by AI): already-paid, possible duplicate,
    amount-over-balance, repudiated,
  * the AI summary/opinion IF the off-peak job (presummarise_open_claims) has
    written one onto the claim — a stored string, advisory only.

The ONE safety rule: facts and flags are Omni's own arithmetic; the AI only ever
supplies words it wrote earlier. Reads the local mirror, so it works even if
Graphite is unreachable; a stale mirror is flagged, never blocking.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db.models import F

from integrations.premium_gate import classify_premium_gate

log = logging.getLogger(__name__)

# Status keywords (matched case-insensitively as substrings of GraphiteClaim.status).
_PAID_STATES = ('paid', 'settled', 'closed')
_REPUDIATED_STATES = ('repudiat', 'declin', 'reject')


def _dp(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _fmt_money(currency: str, value) -> str:
    return f'{currency} {_dp(value):,.2f}'


def build_facts_text(claim, currency: str = 'BWP') -> str:
    """One-paragraph, plain-English description of the claim from the mirror —
    used to auto-fill the payment line description and shown in the insight box.
    Deterministic; no AI."""
    who = claim.customer_name or 'Unknown customer'
    kind = 'company' if claim.is_company else 'individual'
    bits = [f'Claim {claim.claim_number} — {who} ({kind}).']
    ident = []
    if claim.policy_number:
        ident.append(f'policy {claim.policy_number}')
    if claim.product_name:
        ident.append(claim.product_name)
    if ident:
        bits.append(' / '.join(ident) + '.')
    if claim.date_of_loss:
        loss = f'Loss {claim.date_of_loss:%Y-%m-%d}'
        if claim.damage_cause:
            loss += f': {claim.damage_cause}'
        bits.append(loss + '.')
    elif claim.damage_cause:
        bits.append(f'{claim.damage_cause}.')
    if claim.status:
        bits.append(f'Status {claim.status}.')
    bits.append(
        f'Reserve {_fmt_money(currency, claim.total_reserve)}, '
        f'paid {_fmt_money(currency, claim.total_payment)}, '
        f'balance {_fmt_money(currency, claim.balance)}.'
    )
    if claim.claim_handler:
        bits.append(f'Handler {claim.claim_handler}.')
    return ' '.join(bits)


# Omni requests that never paid anything — they do not count as "Omni has paid".
_OMNI_DEAD_OR_DRAFT = ('rejected', 'cancelled', 'draft')

# A mirror row older than this is flagged so the reader knows the Graphite
# figures may have moved (the 14-Aug → 4-Sep freeze hid behind a red "paid").
STALE_AFTER_DAYS = 3

CLAIM_EXCEPTION_CONTROL = 'PAY-CLAIM-01'


def graphite_shows_settled(claim) -> bool:
    """Graphite's own view: a loss payment posted, or a paid/settled/closed
    status. Graphite posts the loss payment when Claims raises it — it is NOT
    evidence the bank paid anyone."""
    status_l = (claim.status or '').lower()
    return _dp(claim.total_payment) > 0 or any(s in status_l for s in _PAID_STATES)


def omni_payments_for_claim(claim_number: str) -> list[tuple[str, str, str]]:
    """Omni's own register: live or paid requests carrying this claim number
    (any amount), newest first, as (ref, status, payee). Rejected, cancelled and
    draft requests never paid anything and are ignored. The payee lets the caller
    tell a repeat of the SAME supplier (a real duplicate) from a DIFFERENT
    supplier on the same claim (windscreen / engine / assessment — CFO 2026-09-02,
    the legitimate multi-supplier claim PAY-DUP-01 already treats as soft)."""
    from taskboard.models import PaymentRequest
    cn = (claim_number or '').strip()
    if not cn:
        return []
    return list(PaymentRequest.objects
                .filter(line_items__icontains=cn)
                .exclude(status__in=_OMNI_DEAD_OR_DRAFT)
                .order_by('-created_at')
                .values_list('ref', 'status', 'payee')[:5])


def graphite_shows_finished(claim) -> bool:
    """The ODD case (CFO 2026-09-04): the claim is already CLOSED in Graphite, or
    repudiated/declined. Paying a finished claim is unusual. A loss payment
    posted on a claim that is still open is the NORMAL sequence — Claims posts
    it, then Finance pays it — and is deliberately NOT in here: last month 50 of
    53 claim lines were in that state, so routing them would make the committee
    a standing third approval on nearly every claims payment."""
    status_l = (claim.status or '').lower()
    return 'closed' in status_l or any(s in status_l for s in _REPUDIATED_STATES)


# --- Premium "Gate 0" (Kago Tshutlhedi memo v2, 6-Sep-2026) ---------------------
# Does the policy's debit order show premium received for the period of loss?
# Reads the RealPay debit-order mirror (source='transaction' = the per-policy
# outcome). RealPay's ClientNumber IS the Graphite policy number (realpay/recon.py),
# space+dash-normalised. NEVER a red on missing data — a policy we hold no debit
# rows for gets NO card (the recon burn: a bad/empty join must not paint danger).
_GATE_CARD_LEVEL = {'green': 'info', 'amber': 'warning', 'red': 'danger'}


def premium_gate_flag(claim) -> dict | None:
    """The premium Gate-0 card for a claim, or None when it cannot be assessed
    (no policy number, no loss date, or no debit-order record for the policy).
    An ``info`` card is returned when the debit-order mirror does not reach the
    loss date — we say "cannot assess", never a false lapse."""
    if not getattr(claim, 'policy_number', '') or not getattr(claim, 'date_of_loss', None):
        return None
    # space+dash normalised — matches realpay/recon._norm_contract for the real
    # cases (policy numbers only ever differ by spaces/dashes, e.g. 'COMG 2025 1').
    key = str(claim.policy_number or '').upper().replace(' ', '').replace('-', '')
    if not key:
        return None
    try:
        from django.db.models import Max, Value
        from django.db.models.functions import Replace, Upper
        from realpay.models import RealPayTransaction

        txn = RealPayTransaction.objects.filter(source='transaction', txn_date__isnull=False)
        # Coverage guard: the row-level RealPay mirror is hand-loaded and can lag.
        # If it does not reach the loss date we CANNOT assess premium — never paint
        # a false lapse just because the data stops earlier (absence != non-payment).
        data_to = txn.aggregate(m=Max('txn_date'))['m']
        if data_to is None:
            return {'level': 'info', 'code': 'premium_unassessable',
                    'label': 'Premium Gate 0 — no debit-order data loaded in Omni; premium '
                             'status cannot be assessed. Check RealPay / Graphite before settling.'}
        if data_to < claim.date_of_loss:
            return {'level': 'info', 'code': 'premium_unassessable',
                    'label': f'Premium Gate 0 — debit-order data in Omni ends '
                             f'{data_to:%d %b %Y}; a loss on {claim.date_of_loss:%d %b %Y} '
                             f'cannot be assessed yet. Check RealPay / Graphite before settling.'}
        norm = Replace(Replace(Upper('client_number'), Value(' '), Value('')),
                       Value('-'), Value(''))
        rows = (txn.annotate(_pol=norm).filter(_pol=key)
                .values('txn_date', 'current_status', 'installment_amount'))
        debits = [{'due_date': r['txn_date'], 'status': r['current_status'],
                   'amount': r['installment_amount']} for r in rows]
        if not debits:
            return None  # no debit-order record for this policy — can't assess, no card
        res = classify_premium_gate(claim.date_of_loss, debits)
    except Exception:  # noqa: BLE001 — the assist box must never break the form
        log.exception('premium_gate_flag failed for claim %s',
                      getattr(claim, 'claim_number', '?'))
        # A blocking control feeds off this (PAY-PREM-01): a system error must be
        # VISIBLE, not a silent "no card" that reads as green. Show an amber
        # "could not check" — never a false lapse (premium_gate_exception_reason
        # only blocks on lapsed/unpaid, so an error still does not block).
        return {'level': 'warning', 'code': 'premium_unassessable',
                'label': 'Premium Gate 0 — the premium check could not run (system error); '
                         'premium status cannot be assessed. Check RealPay / Graphite before settling.'}
    return {
        'level': _GATE_CARD_LEVEL.get(res['level'], 'info'),
        'code': res['code'],
        'label': 'Premium Gate 0 — ' + res['label'],
    }


PREMIUM_EXCEPTION_CONTROL = 'PAY-PREM-01'


def premium_gate_exception_reason(claim_number: str) -> str:
    """PAY-PREM-01 (Kago Tshutlhedi memo v2, 6-Sep-2026, GC 3.B). The premium for
    the period of loss was NOT received (red: lapsed, or the loss falls in an
    unpaid period). The claims payment does not proceed straight through — it is
    entered as an EXCEPTION the committee can release, and the committee cannot
    release it without the bank-error proof attached (enforced in
    payment_exception_signoff). Returns the reason text (carrying the
    '[PAY-PREM-01]' marker so the release gate can spot it), or '' when the gate
    is green / amber / cannot be assessed — never block on missing data."""
    claim = _lookup(claim_number)
    if claim is None:
        return ''
    flag = premium_gate_flag(claim)
    if not flag or flag.get('code') not in ('premium_lapsed', 'premium_unpaid'):
        return ''
    return (f'[{PREMIUM_EXCEPTION_CONTROL}] {flag["label"]} '
            f'Attach the bank-error proof; the committee decides.')


def claim_exception_reason(claim_number: str, currency: str = 'BWP') -> str:
    """PAY-CLAIM-01 (CFO 2026-09-04, Leano Makwapa's report). The claim is
    already closed (or repudiated) in Graphite, yet Omni holds no live or paid
    request for it. That is not a duplicate (there is nothing in Omni to
    duplicate) and it must not block the raiser or paint a red "already paid"
    that reads as a refusal — it is an EXCEPTION the payment committee decides,
    like a changed bank account. Returns the reason text for the record, or ''
    when the rule does not apply (unknown claim, claim still open, or Omni
    already has a request — PAY-DUP-01 owns that one)."""
    claim = _lookup(claim_number)
    if claim is None or not graphite_shows_finished(claim):
        return ''
    if omni_payments_for_claim(claim.claim_number):
        return ''
    return (f'Claim {claim.claim_number} is already "{claim.status or "—"}" in Graphite '
            f'({_fmt_money(currency, claim.total_payment)} posted, balance '
            f'{_fmt_money(currency, claim.balance)}), but Omni has no payment on record '
            f'for it. Paying a finished claim is unusual — confirm with Claims that this '
            f'payment has not already gone out before approving.')


def _hard_flags(claim, *, line_amount, currency: str, entity: str, exclude_pk,
                payee: str = '') -> list[dict]:
    """The pay/hold-and-check FACTS — deterministic, so a reviewer sees the risk
    before reading any AI opinion. levels: danger (red) / warning (amber)."""
    flags: list[dict] = []
    status_l = (claim.status or '').lower()

    # Gate 0 first (Kago memo v2): premium status for the period of loss.
    gate = premium_gate_flag(claim)
    if gate:
        flags.append(gate)

    if graphite_shows_settled(claim):
        omni_hits = omni_payments_for_claim(claim.claim_number) if claim.claim_number else []
        if omni_hits:
            # ONE claim legitimately pays several suppliers (windscreen, engine,
            # assessment report — CFO 2026-09-02). PAY-DUP-01 already lets a
            # different supplier through as SOFT; the insight box must not then
            # shout a red "already paid" that Leano reads as a refusal (bug
            # cbc07b0a, 2026-09-07). So split the existing Omni requests by
            # supplier: same payee back again is the real red duplicate; every
            # existing request being to a DIFFERENT supplier is the normal
            # multi-supplier claim, an informational note only.
            from taskboard.payment_duplicates import _norm
            this_payee = _norm(payee)
            same_payee = [h for h in omni_hits if _norm(h[2]) == this_payee]
            other_payee = [h for h in omni_hits if _norm(h[2]) != this_payee]
            if this_payee and not same_payee:
                seen = ', '.join(f'{(pe or "—")} ({ref})' for ref, _st, pe in other_payee[:3])
                flags.append({
                    'level': 'info', 'code': 'other_supplier_same_claim',
                    'label': f'This claim already has payment(s) to other suppliers: {seen}. '
                             f'That is normal where one claim pays several suppliers — this is a '
                             f'different supplier, so it is not a duplicate. You can submit.',
                })
            else:
                # Same supplier again, or no payee to check by. This is a
                # HEADS-UP, never a refusal (CFO 2026-09-09: Omni never silently
                # blocks a payment — the human decides). A true same-payee,
                # same-amount duplicate is caught separately by PAY-DUP-01 and
                # goes to the committee; here the amount usually differs (a
                # further invoice on the same claim), so it is amber, not red,
                # and says plainly the raiser can still submit.
                seen = ', '.join(f'{ref} ({status})' for ref, status, _pe in omni_hits[:3])
                flags.append({
                    'level': 'warning', 'code': 'already_paid',
                    'label': f'Omni already holds payment request(s) for this claim: {seen}. '
                             f'Graphite: {_fmt_money(currency, claim.total_payment)} posted, '
                             f'balance {_fmt_money(currency, claim.balance)}. Check this is not '
                             f'the same invoice — if it is a further or different invoice you can '
                             f'submit; the approver confirms before it proceeds.',
                })
        elif graphite_shows_finished(claim):
            # Finished in Graphite, nothing in Omni — the odd case. Amber, and
            # the request goes to the payment committee (PAY-CLAIM-01). Never a
            # refusal.
            flags.append({
                'level': 'warning', 'code': 'graphite_closed',
                'label': f'This claim is already "{claim.status}" in Graphite '
                         f'({_fmt_money(currency, claim.total_payment)} posted, balance '
                         f'{_fmt_money(currency, claim.balance)}) but Omni has no payment on record '
                         f'for it. You can still submit — the payment committee will confirm it '
                         f'before it proceeds.',
            })
        else:
            # Graphite's posting on an open claim — the normal sequence (Claims
            # posts, Finance pays). A plain note, nothing more.
            flags.append({
                'level': 'warning', 'code': 'graphite_settled',
                'label': f'Graphite shows a loss payment posted on this claim '
                         f'({_fmt_money(currency, claim.total_payment)}, balance '
                         f'{_fmt_money(currency, claim.balance)}). Omni has no payment on record '
                         f'yet, so this is most likely the payment Claims raised. You can submit.',
            })

    if claim.detail_synced_at is not None:
        from django.utils import timezone as _tz
        age_days = (_tz.now() - claim.detail_synced_at).days
        if age_days > STALE_AFTER_DAYS:
            flags.append({
                'level': 'info', 'code': 'stale_mirror',
                'label': f'Graphite figures last refreshed {claim.detail_synced_at:%d %b %Y} '
                         f'({age_days} days ago) — they may have moved since.',
            })

    if any(s in status_l for s in _REPUDIATED_STATES):
        flags.append({
            'level': 'danger', 'code': 'repudiated',
            'label': f'Claim status is "{claim.status}" — confirm before paying.',
        })

    # Amount vs remaining balance (balance is reserve minus paid on the mirror).
    if line_amount is not None:
        amt = _dp(line_amount)
        bal = _dp(claim.balance)
        if amt > 0 and bal > 0 and amt > bal:
            flags.append({
                'level': 'warning', 'code': 'amount_over_balance',
                'label': f'Payment {_fmt_money(currency, amt)} exceeds the claim\'s remaining '
                         f'balance {_fmt_money(currency, bal)}.',
            })
        elif amt > 0 and bal <= 0 and _dp(claim.total_reserve) > 0 and amt > _dp(claim.total_reserve):
            flags.append({
                'level': 'warning', 'code': 'amount_over_reserve',
                'label': f'Payment {_fmt_money(currency, amt)} exceeds the total reserve '
                         f'{_fmt_money(currency, claim.total_reserve)}.',
            })

    # Possible duplicate — reuse the payment-request duplicate control (PAY-DUP-01),
    # which already recognises Graphite claim numbers. A hard clash = same claim
    # number AND same amount on a live request. Defensive: never let the assist box
    # break because a shared control changed shape.
    if line_amount is not None and _dp(line_amount) > 0 and claim.claim_number:
        try:
            from taskboard.payment_duplicates import find_duplicates
            probe = [{'claim_number': claim.claim_number,
                      'amount': str(_dp(line_amount)),
                      'description': ''}]
            res = find_duplicates(probe, currency=currency, exclude_pk=exclude_pk, entity=entity or '')
            hard = res.get('hard') or []
            if hard:
                refs = []
                for h in hard:
                    # compare_lines hard rows carry 'clash_ref' (payment_duplicates.py),
                    # not 'ref' — keying off 'ref' silently dropped the pointer.
                    ref = h.get('clash_ref') if isinstance(h, dict) else None
                    if ref:
                        refs.append(str(ref))
                detail = (' — see ' + ', '.join(sorted(set(refs))[:3])) if refs else ''
                flags.append({
                    'level': 'danger', 'code': 'duplicate',
                    'label': f'Possible duplicate: same claim & amount already on a live request{detail}.',
                })
        except Exception:  # noqa: BLE001 — assist box must never break the form
            pass

    return flags


def _lookup(claim_number: str):
    """Newest mirror row for a claim number, or None."""
    from integrations.models import GraphiteClaim
    # NULLs LAST explicitly: on Postgres a bare '-detail_synced_at' puts NULLs
    # FIRST, so an unsynced row (zero figures) would beat the real synced one when
    # a claim_number is shared (H96). updated_at is auto_now, never null.
    return (GraphiteClaim.objects
            .filter(claim_number__iexact=claim_number)
            .order_by(F('detail_synced_at').desc(nulls_last=True), '-updated_at')
            .first())


def _empty(claim_number: str) -> dict:
    return {
        'found': False, 'claim_number': claim_number,
        'customer_name': '', 'is_company': False, 'policy_number': '', 'product_name': '',
        'claim_type': '', 'status': '', 'claim_handler': '', 'date_of_loss': None,
        'damage_cause': '', 'total_reserve': '0.00', 'total_payment': '0.00', 'balance': '0.00',
        'currency': 'BWP', 'facts_text': '', 'flags': [],
        'ai_summary': '', 'ai_suggestion': '', 'ai_reason': '', 'ai_summary_at': None,
        'stale': False, 'last_synced': None,
    }


def build_claim_insight(claim_number, *, line_amount=None, currency: str = 'BWP',
                        entity: str = '', exclude_pk=None, payee: str = '') -> dict:
    """The full insight dict for one claim number. Never raises for a normal
    miss — returns ``found: False``. Serves the local mirror only (instant); the
    mirror is refreshed by pull_graphite_claims, not by this request path."""
    cn = (claim_number or '').strip()
    if not cn:
        return _empty('')
    claim = _lookup(cn)
    if claim is None:
        return _empty(cn)

    return {
        'found': True,
        'claim_number': claim.claim_number,
        'customer_name': claim.customer_name,
        'is_company': claim.is_company,
        'policy_number': claim.policy_number,
        'product_name': claim.product_name,
        'claim_type': claim.claim_type,
        'status': claim.status,
        'claim_handler': claim.claim_handler,
        'date_of_loss': claim.date_of_loss.isoformat() if claim.date_of_loss else None,
        'damage_cause': claim.damage_cause,
        'total_reserve': f'{_dp(claim.total_reserve):.2f}',
        'total_payment': f'{_dp(claim.total_payment):.2f}',
        'balance': f'{_dp(claim.balance):.2f}',
        'currency': currency,
        'facts_text': build_facts_text(claim, currency),
        'flags': _hard_flags(claim, line_amount=line_amount, currency=currency,
                             entity=entity, exclude_pk=exclude_pk, payee=payee),
        'ai_summary': claim.ai_summary or '',
        'ai_suggestion': claim.ai_suggestion or '',
        'ai_reason': claim.ai_reason or '',
        'ai_summary_at': claim.ai_summary_at.isoformat() if claim.ai_summary_at else None,
        'stale': claim.detail_synced_at is None,
        'last_synced': claim.detail_synced_at.isoformat() if claim.detail_synced_at else None,
    }
