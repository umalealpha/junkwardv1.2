"""genric/collections.py — the money basis. The bank is the truth.

A policy counts as collected only when the premium actually lands in FNB
63104367974. In July 2026 the bank showed 93 confirmed collections while
Graphite showed 81 "successful" — a 12-policy, R1,188 difference that the
manual process carried. Everything downstream (GWP, the cession, the invoice)
is built off the bank lines, never off Graphite's payment status.

This module does NOT read a bank file. ``banking.models.BankStatement`` /
``BankStatementLine`` is the register, it already has the import, the header
record and the database-level duplicate guard, and ``banking/api_views.py``
already names this pack as a consumer of its date-range read. Building a second
statement reader is how two versions of the same month start disagreeing.

Classification follows the build prompt's table exactly:

    REREALPAY (positive)     RealPay premium credit      include
    REREALPAY (negative)     RealPay reversal            subtract
    PAYAT (positive)         PayAt premium credit        include
    FNBD…                    direct EFT premium          include
    #SERVICE FEES            bank fee                    exclude
    INT ON CREDIT BALANCE    interest                    exclude
    PAYAT…SF                 PayAt service fee / sweep   exclude

Order matters, and it is the opposite of the reading order: the EXCLUSIONS are
tested first. "PAYAT…SF" contains "PAYAT", so a PayAt service fee tested
against the inclusion rules first would be counted as premium. That single
ordering mistake inflates GWP by every fee line in the month.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

from .money import q2


# ── Outcomes ────────────────────────────────────────────────────────────────
INCLUDE = 'include'      # a premium credit — adds to GWP
REVERSAL = 'reversal'    # a RealPay reversal — subtracts from GWP
EXCLUDE = 'exclude'      # fee / interest / sweep — not premium
UNCLASSIFIED = 'unclassified'   # no rule fired; goes to Open Items, never silently dropped


@dataclass(frozen=True)
class ClassifiedLine:
    line_id: str
    transaction_date: date
    description: str
    reference: str
    amount: Decimal
    outcome: str
    rule: str
    channel: str            # 'realpay' | 'payat' | 'eft' | '' for excluded


# ── Rules ───────────────────────────────────────────────────────────────────
# Each rule is (name, compiled pattern, sign requirement, outcome, channel).
# sign: 'positive' | 'negative' | 'any'.

_EXCLUSIONS: Sequence[tuple[str, re.Pattern, str]] = (
    # PayAt service-fee / sweep lines MUST be excluded before the PAYAT
    # inclusion rule can see them. The '…SF' in the source table is a PayAt
    # narrative suffix; match it as a word-ish token so a merchant genuinely
    # called "…SF…" inside a longer word is not swallowed.
    ('PAYAT SERVICE FEE / SWEEP', re.compile(r'PAYAT.*\bSF\b', re.I), 'payat_sf'),
    ('PAYAT SERVICE FEE',         re.compile(r'PAYAT.*SERVICE\s*FEE', re.I), 'payat_sf'),
    ('SERVICE FEES',              re.compile(r'#?\s*SERVICE\s*FEES?', re.I), 'bank_fee'),
    ('INTEREST ON CREDIT BALANCE', re.compile(r'INT(EREST)?\s+ON\s+CREDIT\s+BALANCE', re.I), 'interest'),
)

_INCLUSIONS: Sequence[tuple[str, re.Pattern, str, str]] = (
    # (name, pattern, sign, channel)
    ('REREALPAY CREDIT',   re.compile(r'REREALPAY', re.I), 'positive', 'realpay'),
    ('PAYAT CREDIT',       re.compile(r'PAYAT', re.I),     'positive', 'payat'),
    ('FNBD DIRECT EFT',    re.compile(r'\bFNBD', re.I),    'positive', 'eft'),
)

_REVERSALS: Sequence[tuple[str, re.Pattern, str]] = (
    ('REREALPAY REVERSAL', re.compile(r'REREALPAY', re.I), 'realpay'),
)


def classify_one(description: str, amount, reference: str = '') -> tuple[str, str, str]:
    """Return (outcome, rule_name, channel) for one bank narrative + amount.

    ``reference`` is appended to the text tested because FNB puts the payment
    channel in either field depending on how the credit was originated.
    """
    text = f'{description or ""} {reference or ""}'
    amt = amount if isinstance(amount, Decimal) else Decimal(str(amount or 0))

    for name, pattern, channel in _EXCLUSIONS:
        if pattern.search(text):
            return EXCLUDE, name, channel

    if amt < 0:
        for name, pattern, channel in _REVERSALS:
            if pattern.search(text):
                return REVERSAL, name, channel
        # A debit that is not a known reversal is not premium and not a known
        # fee. It must surface, not vanish.
        return UNCLASSIFIED, '', ''

    for name, pattern, sign, channel in _INCLUSIONS:
        if sign == 'positive' and amt <= 0:
            continue
        if pattern.search(text):
            return INCLUDE, name, channel

    return UNCLASSIFIED, '', ''


def classify_lines(lines: Iterable) -> list[ClassifiedLine]:
    """Classify ``banking.BankStatementLine`` objects (or anything shaped like one)."""
    out: list[ClassifiedLine] = []
    for ln in lines:
        outcome, rule, channel = classify_one(
            ln.description, ln.amount, getattr(ln, 'reference', '') or ''
        )
        out.append(ClassifiedLine(
            line_id=str(getattr(ln, 'id', '')),
            transaction_date=ln.transaction_date,
            description=ln.description or '',
            reference=getattr(ln, 'reference', '') or '',
            amount=q2(ln.amount),
            outcome=outcome,
            rule=rule,
            channel=channel,
        ))
    return out


@dataclass(frozen=True)
class CollectionSummary:
    """What the bank says was collected in the month."""
    confirmed_count: int            # credits counted as premium
    reversal_count: int
    gross_credits: Decimal          # sum of included credits
    reversals: Decimal              # sum of reversals, as a POSITIVE number
    confirmed_gwp_incl_vat: Decimal  # gross_credits - reversals
    excluded_count: int
    excluded_total: Decimal
    unclassified_count: int
    unclassified_total: Decimal
    by_channel: dict

    @property
    def net_collection_count(self) -> int:
        """Collections net of reversals — the count Finance quotes as 'confirmed'."""
        return self.confirmed_count - self.reversal_count


def summarise(classified: Sequence[ClassifiedLine]) -> CollectionSummary:
    """Roll classified lines up to the month's confirmed GWP, VAT inclusive.

    Reversals are SUBTRACTED, not dropped: a premium that came in and went back
    out was never collected, and leaving it in overstates the cession — we would
    invoice GENRIC 90% of money we do not hold.
    """
    gross = Decimal('0.00')
    rev = Decimal('0.00')
    exc = Decimal('0.00')
    unk = Decimal('0.00')
    n_inc = n_rev = n_exc = n_unk = 0
    by_channel: dict = {}

    for c in classified:
        if c.outcome == INCLUDE:
            gross += c.amount
            n_inc += 1
            slot = by_channel.setdefault(c.channel, {'count': 0, 'amount': Decimal('0.00')})
            slot['count'] += 1
            slot['amount'] = q2(slot['amount'] + c.amount)
        elif c.outcome == REVERSAL:
            rev += abs(c.amount)
            n_rev += 1
            slot = by_channel.setdefault(c.channel, {'count': 0, 'amount': Decimal('0.00')})
            slot['count'] -= 1
            slot['amount'] = q2(slot['amount'] - abs(c.amount))
        elif c.outcome == EXCLUDE:
            exc += c.amount
            n_exc += 1
        else:
            unk += c.amount
            n_unk += 1

    return CollectionSummary(
        confirmed_count=n_inc,
        reversal_count=n_rev,
        gross_credits=q2(gross),
        reversals=q2(rev),
        confirmed_gwp_incl_vat=q2(gross - rev),
        excluded_count=n_exc,
        excluded_total=q2(exc),
        unclassified_count=n_unk,
        unclassified_total=q2(unk),
        by_channel=by_channel,
    )
