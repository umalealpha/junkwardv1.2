import re

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .api_views import compute_match_confidence
from .models import BankMatchMemory, BankStatementLine
from payments.models import Payment

NOISE_WORDS = {
    'PAYMENT', 'TRANSFER', 'EFT', 'FNB', 'REF', 'FROM', 'TO', 'POS', 'PURCHASE'
}

def _normalise_key(text: str) -> str:
    if not text:
        return ''
    # Uppercase, strip digits and punctuation, collapse spaces
    text = text.upper()
    text = re.sub(r'[\d\W_]+', ' ', text)
    tokens = text.split()
    return ' '.join(tokens)

def normalise_counterparty(description: str) -> str:
    """Normalise a bank statement description into a counterparty key."""
    norm_text = _normalise_key(description)
    tokens = [t for t in norm_text.split() if t not in NOISE_WORDS]
    # Keep first 3 remaining tokens
    return ' '.join(tokens[:3])

def normalise_payee(name: str) -> str:
    """Normalise a payee/contact name into a payee key."""
    return _normalise_key(name)

def remember(line: BankStatementLine, payment: Payment, user):
    """
    Upsert a BankMatchMemory record for a confirmed match.
    This should be called within a transaction.
    """
    if not payment.contact or not payment.contact.name:
        return

    company = line.statement.bank_account.gl_account.owner_company
    if not company:
        return

    # Cut to the column sizes: Postgres rejects an over-long value, and that
    # error would sit inside the match transaction (sqlite never enforces it).
    counterparty_key = normalise_counterparty(line.description)[:120]
    payee_key = normalise_payee(payment.contact.name)[:200]

    if not counterparty_key or not payee_key:
        return

    with transaction.atomic():
        memory, created = BankMatchMemory.objects.select_for_update().get_or_create(
            company=company,
            counterparty_key=counterparty_key,
            payee_key=payee_key,
        )
        memory.times_confirmed = F('times_confirmed') + 1
        memory.last_confirmed_at = timezone.now()
        memory.last_confirmed_by = user
        memory.save(update_fields=['times_confirmed', 'last_confirmed_at', 'last_confirmed_by', 'updated_at'])

def suggest(line: BankStatementLine, candidates: list[Payment]) -> dict:
    """
    Generate match suggestions for a bank statement line.
    Returns a dict with 'suggestions' list and 'agrees_with_deterministic'.
    """
    stmt_company = line.statement.bank_account.gl_account.owner_company
    if not stmt_company:
        return {'suggestions': [], 'agrees_with_deterministic': True}

    counterparty_key = normalise_counterparty(line.description)[:120]

    payee_keys = [normalise_payee(p.contact.name)[:200] for p in candidates if p.contact]
    # (company, counterparty_key, payee_key) is unique, so one row per payee here.
    memories = {m.payee_key: m for m in BankMatchMemory.objects.filter(
        company=stmt_company,
        counterparty_key=counterparty_key,
        payee_key__in=payee_keys,
        disabled=False,
    )}

    suggestions = []
    for payment in candidates:
        if not payment.company or payment.company.id != stmt_company.id:
            continue

        payee_key = normalise_payee(payment.contact.name if payment.contact else '')[:200]
        memory = memories.get(payee_key)
        memory_hits = memory.times_confirmed if memory else 0

        date_gap = abs((payment.payment_date - line.transaction_date).days)
        line_refs = [t.strip().lower() for t in (line.reference, line.description) if (t or '').strip()]
        pay_refs = [t.strip().lower() for t in (payment.reference, payment.payment_number) if (t or '').strip()]
        reference_match = any(a in b or b in a for a in line_refs for b in pay_refs)

        deterministic_confidence, details = compute_match_confidence(
            line.amount, payment.amount, date_gap, reference_match
        )

        score = deterministic_confidence + min(memory_hits, 5) * 4
        score = min(score, 100)

        expl_parts = []
        if details['amount_equal']:
            expl_parts.append("Amount equal")
        else:
            expl_parts.append("Amount differs")
        
        day_str = 'day' if date_gap == 1 else 'days'
        expl_parts.append(f"{date_gap} {day_str} apart")

        if details['reference_match']:
            expl_parts.append("reference matches.")
        else:
            expl_parts.append("reference does not match.")
        
        explanation = ', '.join(expl_parts).replace(' ,', ',').replace('.,', '.')

        if memory and memory_hits > 0:
            last_by_user = memory.last_confirmed_by
            last_by = last_by_user.get_short_name() if last_by_user and last_by_user.get_short_name() else 'a user'
            last_date_str = memory.last_confirmed_at.strftime('%d %b') if memory.last_confirmed_at else 'previously'
            plural = 's' if memory_hits > 1 else ''
            explanation += f" Matched to this payee {memory_hits} time{plural} before (last {last_date_str} by {last_by})."

        suggestions.append({
            'payment_id': str(payment.id),
            'payment_number': payment.payment_number or '',
            'payee': payment.contact.name if payment.contact else '',
            'amount': str(payment.amount),
            'payment_data_for_sort': (deterministic_confidence, str(payment.id)),
            'deterministic_confidence': deterministic_confidence,
            'memory_hits': memory_hits,
            'score': score,
            'explanation': explanation,
        })

    suggestions.sort(key=lambda x: x['score'], reverse=True)
    top_suggestions = suggestions[:5]
    
    top_deterministic = sorted(suggestions, key=lambda x: x['payment_data_for_sort'], reverse=True)

    agrees = True
    if top_suggestions and top_deterministic:
        if top_suggestions[0]['payment_id'] != top_deterministic[0]['payment_id']:
            agrees = False
    elif bool(top_suggestions) != bool(top_deterministic):
        agrees = False

    for s in top_suggestions:
        del s['payment_data_for_sort']
    
    return {
        'suggestions': top_suggestions,
        'agrees_with_deterministic': agrees,
    }
