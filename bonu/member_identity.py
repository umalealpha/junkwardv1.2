"""
bonu/member_identity.py — knowing it is the SAME member, without keeping their name.

CFO 2026-08-03: *"damn member names is important how can we know which member is a fraud"*
and *"we need a guardrail to control the members who doesn't pay."*

He is right, and my first pass was wrong. I discarded the member name entirely, which
protected privacy and simultaneously destroyed the single most valuable fraud test there is:
**the same person claiming again and again, often through different firms.** No rate check,
no Benford test and no AI will ever find that, because each individual invoice looks fine.

The answer is not to store names. It is to store a **one-way token** of the name:

    "…" ─► normalise ─► HMAC-SHA256 with a secret salt ─► M-4f2a9c81b0e7

Two bills for the same person produce the same token, so the system can say *"this member
appears on five matters across three firms"* — and nobody, including an administrator reading
the database, can turn the token back into a name. The salt lives in one database row created
once at random, so the tokens stay stable for ever (deriving it from SECRET_KEY would silently
break every historical join the day that key is rotated).

**What this is not.** A token is not a member number. The real reference lives in Graphite,
the policy system, and matching a token to a policy — and therefore to whether that member's
premium is actually PAID — is the next step and needs the Graphite member lookup. This module
deliberately stops at "same person, yes or no", which is already enough to raise a question.

AD-POL-AI-GOV-001: the name is used in memory to compute the token and is never written to a
column, a log, an export or an AI prompt.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata

TOKEN_PREFIX = 'M-'
TOKEN_LENGTH = 12

# Wording that appears in the same field as the member's name on an Odoo bill and is
# emphatically not a member.
NOT_A_MEMBER = (
    'ALPHA DIRECT', 'INSURANCE COMPANY', 'PTY', 'LTD', 'LIMITED', 'BONU', 'INVOICE',
    'BILL', 'ATTORNEYS', 'ATTORNEY', 'LAW', 'LEGAL', 'PARTNERS', 'ASSOCIATES', 'CHAMBERS',
    'COMPANY', 'AND CO', 'N/A', 'NONE',
)
MIN_NAME_LENGTH = 5
# Words that mean "an organisation", checked token by token so a firm cannot pass as a person.
FIRM_WORDS = frozenset({'CO', 'INC', 'PTY', 'LTD', 'LLP', 'LLC', 'TRUST', 'GROUP',
                        'PRACTICE', 'FIRM', 'OFFICE', 'OFFICES', 'CONSULTANTS'})


def normalise(name: str) -> str:
    """A stable spelling of a person's name.

    Uppercased, accents removed, punctuation dropped, and the words SORTED — so
    "RALOLEMO KENEETSWE" and "Keneetswe Ralolemo" reach the same token. Without the sort,
    one firm writing the surname first would look like a different person, which is exactly
    the gap a repeat claimer would walk through.
    """
    s = unicodedata.normalize('NFKD', name or '')
    s = ''.join(c for c in s if not unicodedata.combining(c)).upper()
    s = re.sub(r'[^A-Z ]', ' ', s)
    words = [w for w in s.split() if len(w) > 1]
    return ' '.join(sorted(words))


def looks_like_a_member(name: str) -> bool:
    """Is this actually a person, or the company / firm / boilerplate in the same field?"""
    raw = (name or '')
    # An ampersand is a partnership, not a person — "Monthe Marumo & Co" slipped through a
    # word-list check because none of its words are on the list.
    if '&' in raw:
        return False
    n = normalise(raw)
    if len(n) < MIN_NAME_LENGTH or len(n.split()) < 2:
        return False
    if any(w in FIRM_WORDS for w in n.split()):
        return False
    return not any(bad in raw.upper() for bad in NOT_A_MEMBER)


def token(name: str, salt: str) -> str:
    """One-way token. Same person → same token; token → name is not possible."""
    n = normalise(name)
    if not n or not salt:
        return ''
    digest = hmac.new(salt.encode(), n.encode(), hashlib.sha256).hexdigest()
    return f'{TOKEN_PREFIX}{digest[:TOKEN_LENGTH]}'


def member_name_from_bill(description: str) -> str:
    """Pull the member's name out of an Odoo bill description, or return ''.

    The shape is:
        "Odoo BILL/2025/07/0005 — INVOICE NO: 5131 - / ALPHA DIRECT … (PTY) LTD / <MEMBER>"
    and it is often repeated twice. The member is the last '/' segment; everything else is
    the company or the reference. Returned for tokenising ONLY — never for storage.
    """
    if not description:
        return ''
    text = description.split('—')[-1] if '—' in description else description
    parts = [p.strip(' -–\t') for p in text.split('/')]
    for part in reversed(parts):
        if looks_like_a_member(part):
            return part
    return ''


def get_salt():
    """The one stable salt, created once at random and never rotated.

    Rotating it would change every token and silently break every same-member join already
    made, so there is deliberately no way to change it from the application.
    """
    from bonu.models import MemberTokenSalt
    row = MemberTokenSalt.objects.first()
    if row is None:
        import secrets
        row = MemberTokenSalt.objects.create(salt=secrets.token_hex(32))
    return row.salt


def token_for_bill(description: str) -> str:
    """Description → token, in one call. '' when there is no member to be found."""
    name = member_name_from_bill(description)
    if not name:
        return ''
    return token(name, get_salt())
