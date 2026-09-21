"""bonu/legal_rules.py — the rules behind Legal claim intake and bill capture.

Kelvin Kimani's spec (9 Sep 2026) adds five things to the Legal section that are
all arithmetic or matching rather than screens: the fixed Botswana region list,
the per-client spend cap and its amber step, days-to-process, the duplicate
guard on a bill, and matching a bill that arrives carrying a client's NAME
instead of our number.

They live here, apart from the views and the models, because each one is a
decision that has to be provable on its own. A cap tier and a name match are
exactly the sort of thing that is easy to get subtly wrong in a template and
impossible to test once it is embedded in one.

TWO RULES RUN THROUGH ALL OF THEM
---------------------------------
1. **An unknown answer is never a safe default.** An unrecognised town comes
   back as "refuse this", not as Gaborone and not as "Other". A claim with no
   received date reports "not known", not nought days. A name that could be two
   different people is ambiguous, never the first of them. A control that can be
   satisfied by a fallback is not a control — that mistake has already cost two
   days here (the entity-code bug, 24 Jul 2026, and the claims-are-ADIC rule,
   29 Jul 2026; checklist L6).
2. **Money is Decimal, and it is never rounded to make a sum work.** A split
   that does not add up is an error the person must fix, not something to
   absorb into the last line.

Nothing in this module touches the database, the general ledger, or any money.
"""
from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal

from django.utils import timezone

from bonu.member_identity import normalise

# ---------------------------------------------------------------------------
# Region — a fixed list, because free text does not group
#
# The spec is explicit that this is a dropdown of Botswana cities and towns and
# not a text box: the whole reason for capturing it is to group matters in
# reporting, and "Gabs" / "Gaborone " / "gaborone" are three groups.
# ---------------------------------------------------------------------------

BOTSWANA_REGIONS: tuple[str, ...] = (
    'Gaborone', 'Francistown', 'Molepolole', 'Maun', 'Serowe', 'Selebi-Phikwe',
    'Kanye', 'Mochudi', 'Mahalapye', 'Mogoditshane', 'Palapye', 'Lobatse',
    'Ramotswa', 'Tlokweng', 'Jwaneng', 'Kasane', 'Letlhakane', 'Orapa',
    'Ghanzi', 'Sowa Town',
    # The catch-all is a deliberate CHOICE on the list, not what an unknown
    # value silently becomes. Somebody picking "Other" is telling us something;
    # a typo quietly becoming "Other" is telling us nothing.
    'Other',
)

REGION_CHOICES = tuple((r, r) for r in BOTSWANA_REGIONS)

_REGION_LOOKUP = {r.casefold(): r for r in BOTSWANA_REGIONS}


def clean_region(value) -> str | None:
    """Canonical region name, '' for blank, or **None** for unrecognised.

    Three outcomes, not two, because the caller has to be able to tell "the user
    left it empty" (fine, region is optional) from "the user sent something we
    do not recognise" (refuse it, 400) — and neither of those may end up
    recorded as a real town.
    """
    if value is None:
        return ''
    if not isinstance(value, str):
        return None
    key = value.strip().casefold()
    if not key:
        return ''
    return _REGION_LOOKUP.get(key)


# ---------------------------------------------------------------------------
# The per-client spend cap
# ---------------------------------------------------------------------------

def _money(value) -> Decimal:
    """A Decimal, refusing a float.

    A float is refused rather than converted because 0.1 + 0.2 is not 0.3, and
    a cap is a comparison against a boundary: the one place a hidden binary
    fraction decides amber against red.
    """
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise TypeError(f'amount must be a Decimal, int or str, not {type(value).__name__}')
    return Decimal(str(value))


def cap_tier(total, amber, cap) -> str:
    """'red' at or above the cap, 'amber' at or above the warning, else 'clear'.

    The cap is tested FIRST so that a mis-typed amber level set above the cap
    can never report a real breach as a mere warning. The thresholds are
    editable on screen, which means they can be edited wrongly.
    """
    total, amber, cap = _money(total), _money(amber), _money(cap)
    if total >= cap:
        return 'red'
    if total >= amber:
        return 'amber'
    return 'clear'


CAP_LABELS = {
    'clear': '',
    'amber': 'Approaching cap',
    'red': 'Cap reached',
}


# ---------------------------------------------------------------------------
# Days to process
# ---------------------------------------------------------------------------

def days_to_process(received_on, closed_on, as_of=None):
    """Calendar days from receipt — live while open, frozen at closure.

    Returns **None** when we were never told when the claim arrived. An unknown
    start must not read as "processed the same day"; that is the identical trap
    `LegalInvoiceSaving.turnaround_days` already guards against, where an
    unknown turnaround must never count as a met SLA.

    Calendar days, per the spec's default. A negative span (a closure date
    before the receipt date — a typo) clamps to nought rather than reporting a
    matter closed before it arrived.
    """
    if received_on is None:
        return None
    # Botswana's today, not the box's. The prod box runs UTC and
    # TIME_ZONE is Africa/Gaborone, so between 00:00 and 02:00 the server
    # clock still reads yesterday and a claim received today comes back as
    # -1 days — which max(0, ...) below then flattens to 0, reading as
    # "processed the same day". A met SLA that never happened is exactly
    # what this function's own docstring says it must never produce.
    end = closed_on if closed_on is not None else (as_of or timezone.localdate())
    return max(0, (end - received_on).days)


# ---------------------------------------------------------------------------
# Duplicate guard on a bill
#
# The spec: the same bill keyed twice would double-count against the cap, so
# flag a suspected duplicate on firm + amount + reference BEFORE it is
# committed. Deliberately a FLAG and not a unique constraint — a firm can
# legitimately re-issue, and refusing the entry outright would push the bill
# out of Omni, which makes every cap total wrong (the worse failure).
# ---------------------------------------------------------------------------

def bill_dup_key(firm, amount, reference) -> str:
    """A stable key for 'the same bill', tolerant of how it was typed.

    Case, spacing and punctuation in the firm name and the reference are
    normalised away — "INV-001" and "inv 001" are the same bill number — but
    the AMOUNT is exact to the cent, because two bills from one firm under one
    reference for different money are two different bills.
    """
    def flat(s) -> str:
        return ''.join(ch for ch in str(s or '').upper() if ch.isalnum())

    money = _money(amount if amount is not None else 0).quantize(Decimal('0.01'))
    raw = f'{flat(firm)}|{money}|{flat(reference)}'
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Matching a bill that carries a NAME instead of our number
#
# Bills arrive from firms with the client's name on them, so a name has to be
# able to find the right existing client. The normaliser is deliberately the
# one already in `bonu/member_identity.py` — it uppercases, strips accents and
# punctuation, and SORTS the words, so a firm writing the surname first still
# reaches the same client. Re-implementing that here would create a second
# spelling of "the same person", and two answers to that question is how the
# wrong client gets charged.
#
# THE SAFETY RULE: a name alone never auto-commits money against a client when
# there is any doubt at all. Two clients can share a name and firms misspell.
# ---------------------------------------------------------------------------

EXACT = 'exact'
AMBIGUOUS = 'ambiguous'
NONE = 'none'


def match_client_name(name, candidates) -> dict:
    """Match a billed name against known clients. Three outcomes, never two.

    `candidates` is a list of dicts carrying at least `id` and `name`.

    * ``exact``     — one and only one client's name normalises to the same
                      thing. Safe to tie the bill automatically.
    * ``ambiguous`` — more than one exact match, or a partial match where every
                      word given is part of a client's name (a firm sending
                      "Naledi Sebina" for "Naledi Kgosi Sebina"). A person
                      picks; the system does not guess.
    * ``none``      — nothing matched. The bill goes to the unallocated
                      exception list, never onto a best guess.

    A single word never returns ``exact``, however unique it looks. A surname on
    its own is not an identification.
    """
    key = normalise(name or '')
    words = set(key.split())
    if not words:
        return {'outcome': NONE, 'matches': []}

    exact, partial = [], []
    for c in candidates or []:
        c_key = normalise((c or {}).get('name') or '')
        if not c_key:
            continue
        if c_key == key and len(words) >= 2:
            exact.append(c)
        elif words and words < set(c_key.split()):
            # Every word we were given appears in this client's name, but the
            # client has more of them. A likely match, never a certain one.
            partial.append(c)

    if len(exact) == 1:
        return {'outcome': EXACT, 'matches': exact}
    if exact:
        return {'outcome': AMBIGUOUS, 'matches': exact}
    if partial:
        return {'outcome': AMBIGUOUS, 'matches': partial}
    # Nothing exact and nothing partial. One last, weaker pass: a single word or
    # a name where the firm has EXTRA words we do not hold ("Mr Thabo Moeng").
    loose = [c for c in (candidates or [])
             if (cw := set(normalise((c or {}).get('name') or '').split()))
             and (cw < words or (len(words) == 1 and words < cw))]
    if loose:
        return {'outcome': AMBIGUOUS, 'matches': loose}
    return {'outcome': NONE, 'matches': []}


# ---------------------------------------------------------------------------
# Splitting one bill across several matters
#
# The load-bearing question in the spec: a firm can send one invoice covering
# several matters, and dumping the whole amount on one of them would charge the
# wrong client. So a bill is captured as a total plus its allocations, and the
# allocations must account for the total exactly.
# ---------------------------------------------------------------------------

def check_split(total, allocation_amounts) -> str | None:
    """None when the split is sound, otherwise the plain-English problem.

    Refuses a rounding gap rather than absorbing it. Three matters sharing
    P1,000.00 cannot each take P333.33 — the missing thebe has to be put
    somewhere by the person capturing it, because only they know which matter
    it belongs to.
    """
    want = _money(total)
    amounts = []
    for value in allocation_amounts or []:
        amount = _money(value)
        if amount <= 0:
            return 'Every matter on the bill must carry an amount greater than nought.'
        amounts.append(amount)

    if not amounts:
        return 'Say which matter (or matters) this bill is for.'

    got = sum(amounts, Decimal('0'))
    if got != want:
        gap = want - got
        return (f'The matters on this bill add up to {got}, but the bill is {want} '
                f'— a difference of {gap}. Adjust a line so they agree.')
    return None
