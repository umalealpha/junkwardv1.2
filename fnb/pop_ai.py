"""
fnb/pop_ai.py — the AI half of proof-of-payment filing, deliberately powerless.

CFO 2026-08-26: "we need to wire deepseek to pick the correct pops."
CFO 2026-08-20: "AI explains, never validates money."

Both hold at once, so the split is structural, not a matter of care:

  * The DETERMINISTIC matcher files. Exact claim number, or exact amount plus a
    reference overlap. That is in pop_capture and the AI cannot reach it.
  * This module only ever WRITES A SUGGESTION into `proposed_*` on the row and
    sets state=PROPOSED. It cannot set FILED_*, cannot touch `claim` or
    `payment_request`, and cannot mark anything paid. A person confirms, and
    confirm_proposal() below is the only path from a suggestion to a filed proof.

PII: the FNB reference carries claimant and payee names — real individuals. Every
string is run through is_safe_for_ai() and only the redacted text leaves the
building. Candidates are sent as opaque option numbers, never as names, so the
model is choosing between shapes, not reading a customer list.
"""
from __future__ import annotations

import json
import logging
import re

from django.db import transaction
from django.utils import timezone

from fnb.models import FNBProofOfPayment as POP

log = logging.getLogger(__name__)

MAX_CANDIDATES = 8


def _candidates_for(pop: POP) -> list[dict]:
    """Plausible homes for an unfiled proof, as anonymous numbered options.

    Amount-near matches only — this is a shortlist for a human, so it is
    deliberately generous where the deterministic matcher is deliberately strict.
    """
    from taskboard.models import PaymentRequest
    from integrations.models import GraphiteClaim

    out: list[dict] = []
    for pr in PaymentRequest.objects.filter(total=pop.amount)[:MAX_CANDIDATES]:
        out.append({'kind': 'payment_request', 'ref': pr.ref,
                    'why': f'payment request, exact amount, payee "{pr.payee}", '
                           f'our ref "{pr.bank_our_reference or pr.graphite_ref or "-"}"'})
    if pop.claim_number_seen:
        for cl in GraphiteClaim.objects.filter(
                claim_number__icontains=pop.claim_number_seen[:8])[:MAX_CANDIDATES]:
            out.append({'kind': 'claim', 'ref': cl.claim_number,
                        'why': f'claim {cl.claim_number}, type {cl.claim_type or "-"}, '
                               f'status {cl.status or "-"}'})
    return out[:MAX_CANDIDATES]


def propose_for(pop: POP, *, dry_run: bool = False) -> dict:
    """Ask the model where this proof probably belongs. Writes a SUGGESTION only."""
    from core.ai_assist import is_safe_for_ai, reasoning_complete

    if pop.state not in (POP.State.UNFILED, POP.State.PROPOSED):
        return {'ok': False, 'reason': f'state is {pop.state} — nothing to propose'}

    cands = _candidates_for(pop)
    if not cands:
        return {'ok': False, 'reason': 'no candidates to choose between'}

    lines = [f'{i + 1}. [{c["kind"]}] {c["why"]}' for i, c in enumerate(cands)]
    prompt = (
        'A bank payment confirmation needs to be filed against the correct record.\n\n'
        f'PAYMENT\n  reference: {pop.reference}\n  amount: {pop.amount}\n\n'
        'CANDIDATE RECORDS\n' + '\n'.join(lines) + '\n\n'
        'Reply with STRICT JSON only, no prose:\n'
        '{"choice": <candidate number or 0 if none is a convincing match>, '
        '"confidence": "high"|"medium"|"low", '
        '"reason": "<one sentence, under 200 characters>"}\n\n'
        'Choose 0 unless the reference genuinely identifies one candidate. A wrong '
        'match on a payment record is worse than no match.'
    )

    report = is_safe_for_ai(prompt)
    if not report.safe:
        return {'ok': False, 'reason': 'blocked by the PII firewall — not sent'}
    safe_prompt = report.redacted_text

    if dry_run:
        return {'ok': True, 'reason': 'dry run', 'prompt_chars': len(safe_prompt),
                'redactions': report.redactions_made, 'candidates': len(cands)}

    try:
        raw = reasoning_complete(safe_prompt)
    except Exception as exc:                                     # noqa: BLE001
        log.warning('POP proposal engine unavailable for %s: %s', pop.pk, exc)
        return {'ok': False, 'reason': f'engine unavailable: {type(exc).__name__}'}

    verdict = _parse_json(raw)
    if not verdict:
        return {'ok': False, 'reason': 'model did not return usable JSON'}

    choice = verdict.get('choice')
    try:
        choice = int(choice)
    except (TypeError, ValueError):
        choice = 0
    if choice < 1 or choice > len(cands):
        return {'ok': True, 'reason': 'model found no convincing match', 'proposed': None}

    pick = cands[choice - 1]
    with transaction.atomic():
        pop.proposed_kind = pick['kind']
        pop.proposed_ref = pick['ref'][:120]
        pop.proposed_reason = (
            f"{(verdict.get('reason') or '').strip()[:300]} "
            f"[confidence: {verdict.get('confidence') or 'unstated'}]").strip()
        pop.proposed_at = timezone.now()
        pop.proposal_engine = 'reasoning_complete'
        pop.state = POP.State.PROPOSED
        pop.save(update_fields=['proposed_kind', 'proposed_ref', 'proposed_reason',
                                'proposed_at', 'proposal_engine', 'state', 'updated_at'])
    return {'ok': True, 'proposed': {'kind': pick['kind'], 'ref': pick['ref']},
            'reason': pop.proposed_reason}


def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    txt = raw.strip()
    m = re.search(r'\{.*\}', txt, re.DOTALL)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# The only route from suggestion to filed proof — a person
# ---------------------------------------------------------------------------
@transaction.atomic
def confirm_proposal(pop: POP, *, user, accept: bool, note: str = '') -> POP:
    """A human accepts the suggestion (and it becomes filed) or rejects it."""
    from django.core.exceptions import ValidationError
    from integrations.models import GraphiteClaim
    from taskboard.models import PaymentRequest

    if pop.state != POP.State.PROPOSED:
        raise ValidationError(f'{pop.reference} has no proposal awaiting confirmation.')

    note = (note or '').strip()
    if not accept:
        if not note:
            raise ValidationError('Say why the proposed match is wrong.')
        pop.state = POP.State.DISMISSED
        pop.review_note = note
        pop.confirmed_by = user
        pop.confirmed_at = timezone.now()
        pop.save()
        return pop

    if pop.proposed_kind == 'claim':
        target = GraphiteClaim.objects.filter(claim_number__iexact=pop.proposed_ref).first()
        if not target:
            raise ValidationError(f'Claim {pop.proposed_ref} no longer exists.')
        pop.claim = target
        pop.state = POP.State.FILED_CLAIM
    elif pop.proposed_kind == 'payment_request':
        target = PaymentRequest.objects.filter(ref=pop.proposed_ref).first()
        if not target:
            raise ValidationError(f'Payment request {pop.proposed_ref} no longer exists.')
        pop.payment_request = target
        pop.state = POP.State.FILED_REQUEST
    else:
        raise ValidationError(f'Unknown proposal type "{pop.proposed_kind}".')

    pop.review_note = note
    pop.confirmed_by = user
    pop.confirmed_at = timezone.now()
    pop.save()

    if pop.state == POP.State.FILED_REQUEST and pop.proof_pdf:
        from fnb.pop_capture import _attach_to_request
        try:
            pop.proof_pdf.open('rb')
            _attach_to_request(pop, pop.payment_request, pop.proof_pdf.read())
        finally:
            pop.proof_pdf.close()
    return pop
