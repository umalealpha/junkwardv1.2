"""
company_cards/services.py — who may upload, and matching receipts to a statement.

The matcher is deliberately CONSERVATIVE. A wrong auto-match is worse than no
match: it marks a transaction as evidenced when nobody has produced the
receipt, which is the exact hole this module exists to close. So a line is only
matched on an EXACT amount, within a few days, and never when the amount is
ambiguous across several candidates — those are left for a human.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from company_cards.models import CardSpend, CardStatementLine, CompanyCard

# A card transaction posts a day or three after it is made, so the receipt date
# and the statement date rarely agree exactly.
MATCH_WINDOW_DAYS = 5


def cardholder_emails() -> set:
    """Who may upload card spending. CFO 2026-08-07 (four) + Unami 2026-09-05.

    Settings-overridable so a card can be handed over without a code change.
    Everything is still checked against a CompanyCard row, so being on this
    list alone gives nobody a card.
    """
    default = [
        'aiyer@alphadirect.co.bw',          # Arun Iyer, CEO
        'arjuniyer@alphadirect.co.bw',      # Arjun Parameswaran, COO
        'pbeka@alphadirect.co.bw',          # Paul Beka
        'ubutale@alphadirect.co.bw',        # Unami Butale (CFO 2026-09-05)
        'pganesharajah@alphadirect.co.bw',  # Prathap Ganesharajah, CFO
    ]
    raw = getattr(settings, 'COMPANY_CARD_HOLDERS', default) or default
    return {e.strip().lower() for e in raw if e and e.strip()}


def user_is_cardholder(user) -> bool:
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if CompanyCard.objects.filter(holder=user, is_active=True).exists():
        return True
    return (getattr(user, 'email', '') or '').strip().lower() in cardholder_emails()


def cards_for(user):
    """The active cards this person may post against. Finance and superusers
    see them all so they can fix a mis-filed receipt."""
    qs = CompanyCard.objects.filter(is_active=True).select_related('holder', 'company')
    if user_is_finance(user):
        return qs
    return qs.filter(holder=user)


def user_is_finance(user) -> bool:
    """Who codes the GL account. Reuses the existing finance titles rather than
    inventing a new permission (K1)."""
    from core.models import UserProfile
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return UserProfile.objects.filter(
        user=user, is_active=True,
        title__in=[UserProfile.Title.CFO,
                   UserProfile.Title.FINANCE_MANAGER,
                   UserProfile.Title.FINANCIAL_CONTROLLER,
                   UserProfile.Title.ACCOUNTANT,
                   UserProfile.Title.SENIOR_ACCOUNTANT]).exists()


# ── matching ────────────────────────────────────────────────────────────────

def _candidates(line: CardStatementLine, spends) -> list:
    """Unmatched spends on the same card, same amount, within the window."""
    lo = line.posted_on - dt.timedelta(days=MATCH_WINDOW_DAYS)
    hi = line.posted_on + dt.timedelta(days=MATCH_WINDOW_DAYS)
    amt = abs(Decimal(line.amount))
    return [s for s in spends
            if abs(Decimal(s.amount)) == amt and lo <= s.spent_on <= hi]


@transaction.atomic
def match_statement(statement) -> dict:
    """Attach each statement line to its receipt where that is unambiguous.

    Returns {'matched', 'unmatched', 'ambiguous'}. Never guesses: an amount
    that could be either of two receipts is left for a person, because a wrong
    match silently marks a transaction as evidenced.
    """
    lines = list(statement.lines.select_related('matched_spend'))
    taken = set(
        CardStatementLine.objects
        .filter(statement__card=statement.card, matched_spend__isnull=False)
        .exclude(statement=statement)
        .values_list('matched_spend_id', flat=True))

    spends = [s for s in CardSpend.objects.filter(card=statement.card)
              if s.id not in taken]

    matched = ambiguous = 0
    used: set = set()
    for line in lines:
        if line.matched_spend_id or line.waived:
            continue
        pool = [s for s in _candidates(line, spends) if s.id not in used]
        if len(pool) == 1:
            line.matched_spend = pool[0]
            line.save(update_fields=['matched_spend', 'updated_at'])
            used.add(pool[0].id)
            matched += 1
        elif len(pool) > 1:
            ambiguous += 1

    unmatched = sum(1 for l in statement.lines.all() if l.needs_receipt)
    return {'matched': matched, 'unmatched': unmatched, 'ambiguous': ambiguous}


def missing_receipts(statement) -> list:
    """Statement lines nobody has produced a receipt for. This is the output
    the whole module exists for."""
    return [l for l in statement.lines.select_related('matched_spend')
            if l.needs_receipt]


def rematch_card(card) -> None:
    """Re-run the matcher across this card's statements. Called after a spend is
    created or explained, so a receipt that answers a known gap clears it at
    once instead of waiting for the next statement upload. Bounded: a card has a
    handful of statements."""
    for st in card.statements.all():
        match_statement(st)


# ── the cardholder's "bills to explain" queue ────────────────────────────────
#
# The exec side of the loop (CFO 2026-09-05). An item is OPEN for the cardholder
# when it still needs them: a spend Finance has queried, a spend without a real
# 25-word explanation yet, or a statement line with no receipt at all. Coded
# spends are Finance's business, done — never the exec's.

def open_spends_for(user) -> list:
    """This cardholder's spends that still need them — queried, or not yet
    explained to 25 words. Coded spends are excluded (Finance closed them)."""
    from company_cards.models import CardSpend
    qs = (CardSpend.objects.filter(card__holder=user)
          .exclude(status=CardSpend.Status.CODED)
          .select_related('card'))
    return [s for s in qs
            if s.status == CardSpend.Status.QUERIED or not s.is_explained]


def open_lines_for(user) -> list:
    """Statement lines on this cardholder's cards with no receipt and not waived."""
    from company_cards.models import CardStatementLine
    return list(CardStatementLine.objects
                .filter(statement__card__holder=user, waived=False,
                        matched_spend__isnull=True)
                .select_related('statement', 'statement__card'))


def open_item_count(user) -> int:
    return len(open_spends_for(user)) + len(open_lines_for(user))


def holders_with_open_items() -> list:
    """Active cardholders who have at least one open item — drives Nudge everyone."""
    holders = {c.holder for c in
               CompanyCard.objects.filter(is_active=True).select_related('holder')}
    return [h for h in holders if open_item_count(h) > 0]
