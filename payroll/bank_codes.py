"""payroll/bank_codes.py — Botswana bank identification from the branch/sort code.

CFO 2026-07-15: derive the bank NAME automatically so staff never type it. The
authoritative key is the **branch/sort code**, not the account number. A Botswana
sort code is 6 digits — the first two identify the bank; the middle two the
branch; the last two the routing sequence (e.g. Absa branch 2901 → sort 290167,
"29" = Absa). Sourced from a bank's own published Botswana sort-code list
(sci.co.bw / Standard Chartered BW), cross-checked against Alpha Direct's own
June salary file (which showed FNB 28xxxx, Absa 29xxxx, Bank Gaborone 20xxxx).

The account-number leading digit corroborates: in Alpha Direct's own FNB
salary-disbursement file, FNB accounts start 6 (e.g. 0062285562106) and Stanbic
accounts start 9 (e.g. 906...). That is used here as a cross-check on the branch
code, and as a fallback when the branch code is missing. Where the branch code
and the account digit disagree, or the branch code is unrecognised, we return no
bank and flag it for a human — we never guess a bank onto payroll banking data.
"""
from __future__ import annotations

import re

# Account-number leading digit -> bank (Alpha Direct FNB salary file, CFO-
# confirmed 2026-07-15). Only the two clearly evidenced; every bank is covered
# by the branch-code table above/below, this just corroborates + is a fallback.
BANK_BY_ACCOUNT_FIRST_DIGIT: dict[str, str] = {
    '6': 'First National Bank Botswana',
    '9': 'Stanbic Bank Botswana',
}

# First two digits of the 6-digit BW sort code -> bank name.
BANK_BY_SORT_PREFIX: dict[str, str] = {
    '28': 'First National Bank Botswana',
    '06': 'Stanbic Bank Botswana',
    '29': 'Absa Bank Botswana',
    '20': 'Bank Gaborone',
    '66': 'Standard Chartered Bank Botswana',
    '55': 'Access Bank Botswana',
    '11': 'Bank of Baroda (Botswana)',
    '80': 'First Capital Bank Botswana',
    '50': 'State Bank of India (Botswana)',
    '91': 'Bank of Botswana',
}


def derive_bank_name(branch_code: str | None) -> str:
    """Return the bank name for a BW branch/sort code, or '' if not recognised.

    Tolerant of how the code was captured — strips spaces/dashes, and handles a
    dropped leading zero (e.g. Stanbic '60601' -> '060601' -> '06') and short
    branch-only forms ('2812' -> '28'). Returns '' (never a guess) for anything
    whose leading digits don't map to a known bank.
    """
    digits = re.sub(r'\D', '', branch_code or '')
    if not digits:
        return ''
    # Check, in order: the digits as-is (canonical 6-digit sort code, e.g.
    # Stanbic '060601' -> '06'); zero-padded to 6 (recovers a DROPPED leading
    # zero, e.g. '60601' -> '060601' -> '06'); leading-zeros stripped (recovers
    # a branch written with a SPURIOUS leading zero, e.g. FNB '02812' -> '2812'
    # -> '28'). First recognised prefix wins; '' if none match — never a guess.
    for candidate in (digits, digits.zfill(6), digits.lstrip('0')):
        prefix = candidate[:2]
        if prefix in BANK_BY_SORT_PREFIX:
            return BANK_BY_SORT_PREFIX[prefix]
    return ''


def account_is_mangled(account_no: str | None) -> bool:
    """True if the account number was corrupted by Excel into scientific
    notation (e.g. '9.06E+12') or otherwise isn't a plain digit string. These
    have LOST digits and must never be imported — the file must be re-exported
    with the account column formatted as Text. (Seen on every Stanbic row of
    the real salary file.)"""
    s = (account_no or '').strip()
    if not s:
        return False   # blank is 'missing', handled separately, not 'mangled'
    return bool(re.search(r'[eE]', s)) or ('.' in s) or not s.lstrip('0').isdigit()


def bank_from_account(account_no: str | None) -> str:
    """Bank from the account's first meaningful digit (leading zeros stripped),
    per BANK_BY_ACCOUNT_FIRST_DIGIT. '' if unknown/mangled."""
    if account_is_mangled(account_no):
        return ''
    digits = re.sub(r'\D', '', account_no or '').lstrip('0')
    if not digits:
        return ''
    return BANK_BY_ACCOUNT_FIRST_DIGIT.get(digits[0], '')


def resolve_bank(account_no: str | None, branch_code: str | None) -> dict:
    """Combine both signals into one verdict for the import preview.

    Returns {bank_name, source, warning}. Branch code is primary; the account
    first-digit corroborates or provides a fallback. Disagreement or a mangled
    account is surfaced as a warning for the human approver — never silently
    resolved.
    """
    by_branch = derive_bank_name(branch_code)
    mangled = account_is_mangled(account_no)
    by_acct = bank_from_account(account_no)

    if mangled:
        return {'bank_name': by_branch, 'source': 'branch' if by_branch else 'none',
                'warning': 'Account number is corrupted (scientific notation / non-numeric). '
                           'Re-export the file with the account column formatted as Text.'}
    if by_branch and by_acct and by_branch != by_acct:
        return {'bank_name': '', 'source': 'conflict',
                'warning': f'Branch code says {by_branch} but the account number looks like '
                           f'{by_acct}. Check the row before approving.'}
    if by_branch:
        return {'bank_name': by_branch, 'source': 'branch', 'warning': ''}
    if by_acct:
        return {'bank_name': by_acct, 'source': 'account',
                'warning': 'Bank inferred from the account number (branch code not recognised).'}
    return {'bank_name': '', 'source': 'none',
            'warning': 'Bank could not be determined — set it manually.'}
