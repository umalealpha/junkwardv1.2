"""
fnb/autocode.py — description-keyword → GL account auto-coding.

Lifted from the ADSA pipeline (bank_statement_parser.py) on 2026-05-18.

The FNB statement view already pulls every transaction line via the
official API; what was missing was the second half — proposing a
contra account for each line so a bookkeeper's 3-hour exercise becomes
a 5-minute review.

The rule registry below is the single source of truth. Order matters:
the FIRST matching rule wins, so put more specific keywords (e.g.
"AGENT TRANSPORTATION") above generic ones (e.g. "TRANSPORT").

Confidence ladder:
    HIGH   — keyword + amount-sign + counterparty all match.
    MEDIUM — keyword matches but counterparty is ambiguous.
    LOW    — only a fuzzy prefix matched.
    UNKNOWN — no rule fired; ledger.journal_entries leaves contra blank.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Sign = Literal['debit', 'credit', 'either']
Confidence = Literal['HIGH', 'MEDIUM', 'LOW', 'UNKNOWN']


@dataclass(frozen=True)
class AutocodeRule:
    keyword: str             # case-insensitive substring match on description
    account_code: str        # the GL the contra side should hit
    sign: Sign = 'either'    # which side of the JE this rule applies to
    confidence: Confidence = 'HIGH'

    def matches(self, description: str, amount: float) -> bool:
        if self.keyword.lower() not in (description or '').lower():
            return False
        if self.sign == 'debit'  and amount > 0: return False
        if self.sign == 'credit' and amount < 0: return False
        return True


# The default Alpha Direct ruleset. Keep specific rules above general
# ones (longest keyword first within a section). Codes are the canonical
# CoA — confirm in Settings > Chart of Accounts before editing.
DEFAULT_RULES: tuple[AutocodeRule, ...] = (
    # ── Bank charges + interest ──────────────────────────────────────
    AutocodeRule('SERVICE FEE',          '670010', 'debit',  'HIGH'),
    AutocodeRule('NON FNB TRANSACTION FEE','670010','debit', 'HIGH'),
    AutocodeRule('BANK CHARGES',         '670010', 'debit',  'HIGH'),
    AutocodeRule('INTEREST DEBIT',       '670020', 'debit',  'HIGH'),
    AutocodeRule('INTEREST CREDIT',      '450010', 'credit', 'HIGH'),

    # ── Payroll / agents ─────────────────────────────────────────────
    AutocodeRule('AGENT TRANSPORTATION', '600002', 'debit',  'HIGH'),
    AutocodeRule('SALARY',               '110001', 'debit',  'HIGH'),
    AutocodeRule('PAYE',                 '152001', 'debit',  'HIGH'),
    AutocodeRule('UIF',                  '152002', 'debit',  'HIGH'),

    # ── Reinsurance / claims ────────────────────────────────────────
    AutocodeRule('REINSURANCE',          '300100', 'either', 'MEDIUM'),
    AutocodeRule('CLAIM SETTLEMENT',     '500001', 'debit',  'HIGH'),
    AutocodeRule('GENRIC',               '445000', 'credit', 'HIGH'),

    # ── Office overheads ─────────────────────────────────────────────
    AutocodeRule('TELKOM',               '630010', 'debit',  'HIGH'),
    AutocodeRule('MTN',                  '630010', 'debit',  'HIGH'),
    AutocodeRule('CELL C',               '630010', 'debit',  'HIGH'),
    AutocodeRule('VODACOM',              '630010', 'debit',  'HIGH'),
    AutocodeRule('ESKOM',                '630020', 'debit',  'HIGH'),
    AutocodeRule('CITY OF',              '630020', 'debit',  'HIGH'),
    AutocodeRule('RENT',                 '620010', 'debit',  'HIGH'),
    AutocodeRule('UBER',                 '600002', 'debit',  'MEDIUM'),
    AutocodeRule('BOLT',                 '600002', 'debit',  'MEDIUM'),

    # ── Premiums in (catch-all for non-Genric inflows) ──────────────
    AutocodeRule('PREMIUM',              '445000', 'credit', 'MEDIUM'),
    AutocodeRule('INSURANCE',            '445000', 'credit', 'LOW'),

    # ── Transfers (booked as inter-account) ──────────────────────────
    AutocodeRule('TRANSFER',             '101499', 'either', 'LOW'),
    AutocodeRule('CASH DEPOSIT',         '280001', 'credit', 'MEDIUM'),
)


def autocode_one(description: str, amount: float,
                 rules: tuple[AutocodeRule, ...] = DEFAULT_RULES) -> dict:
    """Return the best contra-account suggestion for one statement row.

    Output shape:
        {
          'account_code': '630010' | '',
          'confidence':   'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN',
          'rule_matched': 'TELKOM' | None,
        }

    Callers can drop the result straight into a JournalEntryLine
    payload alongside the bank account contra (debit/credit decided by
    sign of `amount`).
    """
    for rule in rules:
        if rule.matches(description or '', amount):
            return {
                'account_code': rule.account_code,
                'confidence':   rule.confidence,
                'rule_matched': rule.keyword,
            }
    return {
        'account_code': '',
        'confidence':   'UNKNOWN',
        'rule_matched': None,
    }


def autocode_many(rows: list[dict], rules: tuple[AutocodeRule, ...] = DEFAULT_RULES) -> list[dict]:
    """Bulk variant — `rows` must each carry `description` + `amount`.

    Returns the same rows with three extra keys merged in.
    """
    out: list[dict] = []
    for r in rows:
        sugg = autocode_one(r.get('description', ''), float(r.get('amount', 0) or 0), rules)
        out.append({**r, **sugg})
    return out
