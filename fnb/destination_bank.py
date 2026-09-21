"""fnb/destination_bank.py — is the payee at FNB Botswana, and is the branch
code one FNB can actually use?

WHY THIS EXISTS (live evidence, 17-Sep-2026). Of the 23 failed FNB batches
holding BWP 677,284.93, ten were AC08 "branch code is invalid or missing".
Omni had validated the DEBTOR branch (ours) since the RMB spec review in May,
but the CREDITOR branch — the payee's — went to the bank with no check at all:

    _branch_src = (vba.branch_code if vba else p.payee_branch_code) or ''
    creditor_branch = _branch_src.strip() or FNB_UNIVERSAL_BRANCH

So these reached FNB exactly as typed and were rejected exactly as you would
expect:

    '6700'         four digits
    an 11-digit value  an ACCOUNT number typed into the branch box
    '64967'        five digits — the leading zero of 064967 was lost
    '202-067'      a hyphen
    '-'            a bare hyphen
    ''             blank on a Stanbic payee, which then silently became FNB's
                   own universal branch 287867 and could only ever be refused

Seven of the ten are caught by the shape rules below. The remaining three were
well-formed six-digit codes that were simply the wrong branch for that account —
no format rule can see that, which is why a rejection routes to the exception
committee rather than being guessed at.

🔴 THIS MODULE NEVER BLOCKS A PAYMENT. CFO, 17-Sep-2026: *"you will never block
a payment, if there is a blocker the exception committee kicks in."* Everything
here returns a PROBLEM SENTENCE or an empty string. The caller turns a problem
into a committee exception (`soft_reasons` in taskboard/payment_views.py), and
three of six named people decide. Nothing in this file refuses anything.

THE FNB TEST IS A POSITIVE MATCH, NOT A SUBSTRING. The check it replaces was
`re.search(r'fnb|first\\s*national', bank_in, re.I)`, which is wrong in both
directions and is the exact anti-pattern the notebook warns about:
  - "FNB SA" matched, so a South African FNB account was told it needed no
    branch code — and Botswana's universal branch 287867 means nothing there.
  - an unknown or misspelt bank matched nothing and fell through to the same
    silent fallback.
Unknown now resolves to NOT FNB Botswana, which asks for a branch code. That is
the safe direction: the worst case is a committee note on a payment that would
have been fine, never a doomed instruction fired at the bank.
"""
from __future__ import annotations

import re
import unicodedata

# Botswana's universal FNB-to-FNB branch. Correct ONLY when the payee really is
# at FNB Botswana (Kabelo Sekoto, RMB, 2026-05-26).
FNB_UNIVERSAL_BRANCH = '287867'

# A branch code FNB accepts is exactly six digits. Leading zeroes are part of
# the code — 064967 is not 64967 — so this is a string rule, never an int.
BRANCH_CODE_LENGTH = 6

# Tokens that say "this is FNB".
_FNB_TOKENS = frozenset({'fnb', 'fnbb', 'firstnational'})
_FNB_PHRASES = ('first national bank', 'first national')

# 🔴 ANOTHER BANK'S NAME WINS OVER THE FNB WORDS, and this is not theoretical:
# three live payment requests are spelt 'Bank Gaborone First National Bank'.
# Read as FNB, a blank branch code on those would have gone to the bank as
# FNB's own 287867 — the exact AC08 this module exists to stop. Found by
# querying production for every live spelling on 17-Sep-2026, which is a check
# worth repeating whenever this list is touched.
#
# 'bank gaborone' is matched as a PHRASE, not as the token 'gaborone', because
# an FNB branch could legitimately be written 'FNB Gaborone' and that is FNB.
_OTHER_BANK_PHRASES = ('bank gaborone',)
_OTHER_BANK_TOKENS = frozenset({
    'stanbic', 'absa', 'barclays', 'chartered', 'access', 'baroda',
    'bancabc', 'nedbank', 'investec', 'zanaco', 'letshego',
})

# Tokens that say "this FNB is not the Botswana one". A branch code is still
# required for those, so they must not pass the FNB test.
#
# This is a DENYLIST and that is defensible here, because FNB is one bank brand
# with a known footprint — FirstRand's retail operations — not an open set. The
# list is every country FNB trades in, so "FNB <somewhere>" that is not Botswana
# is caught. Add a country here if FirstRand opens one.
#
# 🔴 'moçambique' used to be in this list and COULD NEVER MATCH. _tokens splits
# on [^0-9a-z], so the cedilla was a separator and the name became
# ['fnb', 'mo', 'ambique'] — the entry was unreachable and "FNB Moçambique" was
# silently treated as FNB Botswana, which is exactly the blank-branch-to-287867
# path this module exists to close. Found by the off-subscription panel
# (DeepSeek) on 17-Sep-2026 and confirmed by running it. Names are now folded to
# plain ASCII before tokenising, so an accent can no longer hide a country.
_FOREIGN_TOKENS = frozenset({
    'sa', 'rsa', 'southafrica', 'namibia', 'lesotho', 'eswatini', 'swaziland',
    'zambia', 'zimbabwe', 'mozambique', 'mocambique', 'tanzania', 'ghana',
    'india', 'guernsey', 'jersey', 'uk', 'london', 'channelislands',
})

#: Said explicitly, this IS the Botswana one — short-circuits the denylist.
_BOTSWANA_TOKENS = frozenset({'botswana', 'bw'})


def _fold(text: str) -> str:
    """Lower-case and strip accents, so 'Moçambique' -> 'mocambique'.

    Without this an accented country name tokenises into fragments and slips
    past the list above.
    """
    nfkd = unicodedata.normalize('NFKD', (text or '').lower())
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _tokens(bank_name: str) -> list[str]:
    """Lower-case, accent-folded alphanumeric words.

    'FNB- Unicoin' -> ['fnb', 'unicoin']; 'FNB Moçambique' -> ['fnb', 'mocambique'].
    """
    return [t for t in re.split(r'[^0-9a-z]+', _fold(bank_name)) if t]


def is_fnb_botswana(bank_name: str) -> bool:
    """True only when the name positively says FNB **Botswana**.

    Unknown, blank and foreign FNBs all return False, which means "a branch
    code is needed" — never "leave it blank and let the fallback decide".
    """
    name = _fold(bank_name).strip()
    if not name:
        return False
    toks = _tokens(bank_name)
    is_fnb = (any(t in _FNB_TOKENS for t in toks)
              or any(p in name for p in _FNB_PHRASES))
    if not is_fnb:
        return False
    # Another bank named in the same string wins — see _OTHER_BANK_* above.
    if any(t in _OTHER_BANK_TOKENS for t in toks):
        return False
    if any(ph in name for ph in _OTHER_BANK_PHRASES):
        return False
    # "FNB Botswana" said outright wins over everything below.
    if any(t in _BOTSWANA_TOKENS for t in toks):
        return True
    if any(t in _FOREIGN_TOKENS for t in toks):
        return False
    # 'south africa' is two tokens; the joined form catches it.
    if 'south' in toks and 'africa' in toks:
        return False
    # A punctuated sibling of a pinned country — 'FNB S.A.' tokenises to
    # ['fnb', 's', 'a'] and would otherwise slip past the list. One spelling
    # pinned is not the class pinned (checklist L65).
    compact = ''.join(toks)
    if any(compact.endswith(f) for f in _FOREIGN_TOKENS):
        return False
    # A bare 'FNB', or 'FNB- Unicoin', with no country said at all. Domestic —
    # 80 live payment requests are spelt exactly 'FNB' and every one of them is
    # FNB Botswana. Being wrong here costs a needless committee note; being
    # wrong the other way sends a doomed instruction to the bank.
    return True


def branch_code_problem(bank_name: str, branch_code: str) -> str:
    """Plain-English description of what is wrong with this branch code, or ''.

    The sentence is written for the raiser and for the exception committee, so
    it names the value that was actually given. It is never an error code.
    """
    given = (branch_code or '').strip()
    fnb = is_fnb_botswana(bank_name)

    if not given:
        if fnb:
            return ''
        bank = (bank_name or '').strip() or 'this bank'
        return (f'no branch code was given for {bank}. Only an FNB Botswana '
                f'account may be left blank — for anyone else Omni would send '
                f'FNB\'s own branch {FNB_UNIVERSAL_BRANCH}, which the bank '
                f'rejects as AC08.')

    if not given.isdigit():
        return (f'the branch code "{given}" is not all digits. A branch code is '
                f'{BRANCH_CODE_LENGTH} digits with no spaces, dashes or letters.')

    if len(given) != BRANCH_CODE_LENGTH:
        if len(given) == BRANCH_CODE_LENGTH - 1:
            return (f'the branch code "{given}" is {len(given)} digits. Branch '
                    f'codes are {BRANCH_CODE_LENGTH} digits — a leading zero '
                    f'has probably been dropped, so check whether it should be '
                    f'"0{given}".')
        if len(given) > BRANCH_CODE_LENGTH:
            return (f'the branch code "{given}" is {len(given)} digits. Branch '
                    f'codes are {BRANCH_CODE_LENGTH} digits — this looks like '
                    f'an account number typed into the branch box.')
        return (f'the branch code "{given}" is {len(given)} digits. Branch '
                f'codes are {BRANCH_CODE_LENGTH} digits.')

    if given.strip('0') == '':
        return (f'the branch code "{given}" is all zeroes, which the bank '
                f'refuses.')

    return ''


def branch_code_advisory(bank_name: str, branch_code: str) -> str:
    """A NOTE (never a hold) for a branch code that is the right shape but whose
    first two digits match no Botswana bank we know, or ''.

    CFO master M4 (18-Sep-2026): "an ADVISORY warning (not a hard block) for
    valid-format but unknown branch codes". branch_code_problem() already sends
    a wrong-SHAPE code to the committee; this covers the code that passes the
    shape test and is still unrecognised. Foreign banks are skipped — their
    codes are not in the Botswana sort-code table and that is expected.
    """
    given = (branch_code or '').strip()
    if not given or branch_code_problem(bank_name, given):
        return ''
    toks = _tokens(bank_name)
    if any(t in _FOREIGN_TOKENS for t in toks) or ('south' in toks and 'africa' in toks):
        return ''
    from payroll.bank_codes import derive_bank_name
    if derive_bank_name(given):
        return ''
    return (f'the branch code "{given}" is the right shape, but its first two '
            f'digits ({given[:2]}) do not match any Botswana bank Omni knows. '
            'Check it against the payee\'s bank letter before the FNB release. '
            'This is a note, not a hold.')


def usable_branch_code(bank_name: str, branch_code: str,
                       universal: str = FNB_UNIVERSAL_BRANCH) -> str | None:
    """The branch code to put on the instruction, or None if there isn't one.

    None means "do not invent one" — the caller records that and the committee
    supplies the real code. Returning FNB's universal branch for a non-FNB
    payee is what produced the AC08s this module exists to stop.

    `universal` is passed in by build_batch_payload from
    settings.FNB_UNIVERSAL_BRANCH_ID so the debtor and creditor legs of the
    same message can never end up on two different universal branches.
    """
    if branch_code_problem(bank_name, branch_code):
        return None
    given = (branch_code or '').strip()
    return given or (universal or FNB_UNIVERSAL_BRANCH)
