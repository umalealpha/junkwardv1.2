"""Duplicate payment control — PAY-DUP-01 (CFO directive 2026-08-03).

WHY THIS EXISTS
---------------
On 3 Aug 2026 the CFO's authorisation queue held ten requests totalling
BWP 789,626.85. A line-by-line read found BWP 219,600.10 of it was already paid
or double-counted:

  * PAY/ADIC/2026/07/29/0002 (BWP 50,689.84, five Carfil invoices) was a strict
    subset of PAY/ADIC/2026/07/29/0003 — lines 1, 2, 3, 4 and 7, to the cent.
  * PAY/ADIC/2026/07/28/0001 carried five lines already paid days earlier:
    G2026004535 (1,535.98, paid on 07/28/0002), G2026004546 (9,000.00),
    G2026004977 (15,000.00), G2026005105 KOMAROV INVESTMEN (25,684.20) and
    G2026005105 KOMAROV EX-GRATIA (34,220.00) — the last four all on 07/27/0001.
  * G2025003601 CHOPPIES 42,380.04 sat on BOTH 07/23/0001 and 07/28/0001.
  * G2026004524 CARFIL 8,599.94 was paid that morning on 08/03/0001 and still
    appeared on two pending requests.

Nothing in Omni looked at any of it. The terms gate (PAY-SUP-01) checks that an
invoice is DUE; it never asked whether we had already paid it. So the control
here answers exactly one question, at every point where a payment moves forward:

    have we already raised or paid THIS line?

WHAT COUNTS AS A DUPLICATE
--------------------------
A hard clash is the same payment reference AND the same amount, to the cent, on
a request that is live (pending finance, pending CFO) or already paid. Rejected
and cleared/cancelled requests are ignored — those were consciously killed, and
re-raising them is the legitimate flow — WITH ONE EXCEPTION, added 21-Sep-2026:
a closed request the BANK rejected is still watched, because nothing left the
account and the supplier is still owed. See `still_owed()`.

Reference matching is deliberately narrow. Real packs carry the same money under
several labels — description, ref, invoice number, claim number — so every one of
those is harvested per line and normalised (upper-cased, punctuation stripped),
then a Graphite claim number (G2026004287) or a bare invoice number is pulled out
of the free text. A match on ANY harvested token plus an exact amount match is a
clash.

Amount is part of the key on purpose. G2026005105 legitimately carries a repair
(25,684.20) and an ex-gratia (34,220.00) — same claim, two real payments. Keying
on reference alone would block that and the control would be worked around within
a week. Conversely G2026004605 and G2026004619 are both 2,576.00 — same amount,
different claims, also legitimate. It takes BOTH to be a duplicate.

Same-reference-different-amount and same-amount-different-reference are still
surfaced, as soft warnings the approver reads. They do not block.

One claim, several invoices (Pako Kago, 2026-09-02): a claim legitimately
carries multiple invoices — G2026004567 (OMEGA AUTOWORLD) had four, referenced
G2026004567-4546, -4550, … — and equal amounts across them are normal on a
repair. Two lines sharing ONLY the bare claim number at the same amount are NOT
a hard clash when each carries its own different invoice-level identifier (a
suffix on the reference, or an invoice number). If either line has none, or they
share one, it stays hard — a bare repeat of claim+amount is exactly the 3 Aug
pattern. Demoted pairs are still surfaced as soft warnings.

WHO DECIDES A DUPLICATE (CFO 2026-09-04)
----------------------------------------
A hard clash is never refused and never waved through by the raiser. The request
is entered as an EXCEPTION carrying this module's message (which earlier request
holds the money, and its status) and the six-person payment committee decides —
three of six, never the raiser, never the CFO. Once they approve, the sign-off
and mark-as-paid re-checks stand down for that request; a reject ends it. The
free-text override (to 2026-08-09), the four-part override (to 2026-09-02) and
the outright refusal (2 to 4 Sep 2026) are all history: a payment the bank
rejected, or a genuine second leg that happens to match, is a decision for named
people on the record, not a dialog and not a dead end.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Statuses that mean "this money is in flight or gone". A line clashing with one
# of these is a duplicate; a rejected or cancelled request is not.
LIVE_STATUSES = ('pending_finance', 'pending_cfo', 'paid')

# Manus, 2026-08-09: a line must clash against a request in ANY status other than
# rejected or cancelled — a draft or a cleared request still represents the same
# money. LIVE_STATUSES above kept only three, so a duplicate sitting on a draft
# was invisible.
DEAD_STATUSES = ('rejected', 'cancelled')


def still_owed(): 
    """(predicate, annotations) for the closed requests that are STILL OWED.

    A request in DEAD_STATUSES normally drops out of this control — it was
    rejected or pulled, so the money is not going anywhere. That is right when
    a person closed it because the payment was settled or abandoned. It is
    WRONG when it was closed while the bank had thrown the instruction out:
    nothing left the account, the supplier is still owed, and the invoice will
    be raised again. With the request invisible here, that re-raise clashes
    with nothing and a payment already made by hand goes out a second time.

    Measured on production 21-Sep-2026: sixteen requests, BWP 553,301.24, were
    cleared on the CFO's instruction after FNB rejected every instruction
    behind them (eight of them AC08, a wrong or missing branch code). Closing
    them was his call, made with this cost in front of him — this brings the
    control back over that money without reopening a single request.

    Derived, never a list. A request qualifies when a bank instruction behind
    it says the money did not move and none of its instructions settled. It
    therefore covers any future case on its own.

    It applies to REJECTED as well as CANCELLED, deliberately. A request
    rejected at finance sign-off carries a person's "do not pay" — but if the
    bank had already thrown the instruction out, the supplier is still owed and
    the invoice still comes back. Clashing is the safe direction: a hard clash
    ROUTES to the exception committee, it does not block (Omni never blocks a
    payment), and G2026004718 was rejected and then paid three times.

    A closed request with NO batch at all is NOT included. "Nothing was loaded"
    is not "the bank said no", and treating absence as a rejection would drag
    every payment made outside the FNB pipe back into the control.

    🔴 It does NOT self-expire for a request paid by hand outside Omni. The
    only exit is a batch settling against it, which cannot happen on a closed
    request — so that supplier keeps clashing, forever, and a person has to
    say so at the committee. That is the deliberate direction: the alternative
    is silence over money nobody is watching.
    """
    from django.db.models import Exists, OuterRef, Q

    from fnb.models import FNBBatchSubmission as Batch

    # Read the reverse stamp AND the request's own single FK on BOTH legs, for
    # the same reason `_batches_all_rejected` does: the FK holds one batch, and
    # a request processed line by line produces one batch per line. Asymmetry
    # here would keep a request whose FK batch settled while another line
    # failed — money that DID move.
    dead = Exists(Batch.objects.filter(payment_request=OuterRef('pk'),
                                       status__in=Batch.DID_NOT_MOVE))
    settled = Exists(Batch.objects.filter(payment_request=OuterRef('pk'),
                                          status='settled'))
    predicate = ((Q(fnb_batch__status__in=Batch.DID_NOT_MOVE) | Q(_dup_dead_batch=True))
                 & Q(_dup_settled_batch=False)
                 & ~Q(fnb_batch__status='settled'))
    return predicate, {'_dup_dead_batch': dead, '_dup_settled_batch': settled}


def exclude_dead(qs):
    """Drop the closed requests, but KEEP the ones the bank never paid.

    One helper so the two callers of this rule cannot drift apart — the
    duplicate check and the "you loaded this an hour ago" check must agree
    about what counts as live, or a line clashes on one screen and not the
    other (the two-parsers trap).
    """
    from django.db.models import Q

    predicate, annotations = still_owed()
    # alias(), not annotate(): the two EXISTS are only needed by the filter, and
    # annotate() would copy both into the SELECT list of every caller.
    return (qs.alias(**annotations)
              .exclude(Q(status__in=DEAD_STATUSES) & ~predicate))

CONTROL_CODE = 'PAY-DUP-01'

# An override must be a sentence, not a keystroke. Matches the spirit of the
# completion-note minimum already enforced on tasks.
MIN_OVERRIDE_CHARS = 25

# A Graphite claim number: G + 4-digit year + sequence. The single most reliable
# token in a claims pack, and the one the 3 Aug duplicates all shared.
_CLAIM_RE = re.compile(r'\bG\d{4}\d{3,}\b', re.I)

# A bare numeric invoice / voucher number of real length, e.g. 20240956.
_NUMERIC_REF_RE = re.compile(r'\b\d{5,}\b')

# A claim number carrying an invoice-level suffix — 'G2026004567-4546' is one
# specific invoice of claim G2026004567. The suffix is what distinguishes four
# invoices on one claim (Pako Kago, 2026-09-02); harvested from the RAW text
# because after _norm the suffixed form also matches _CLAIM_RE.
_CLAIM_INVOICE_RE = re.compile(r'\bG\d{4}\d{3,}[-/]\d+\b', re.I)

_PUNCT_RE = re.compile(r'[^A-Z0-9]+')


def _norm(value) -> str:
    """Upper-case and strip punctuation so 'G2026004287 CARFIL SERVICES' and
    'g2026004287-carfil  services' collapse to the same string."""
    return _PUNCT_RE.sub('', str(value or '').upper())


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def line_tokens(line: dict) -> set[str]:
    """Every identifier a line can be recognised by.

    Harvested from the four fields that carry a reference in practice, plus any
    claim number or long numeric reference embedded in the free text — because
    the reference is very often ONLY in the description. (On PAY/ADIC/2026/07/24/0001
    every stored `ref` was the same wrong string, '20221134 AUTOSCREEN', while the
    real invoice numbers sat in the descriptions. A control reading `ref` alone
    would have seen twenty identical lines and missed every real one.)
    """
    tokens: set[str] = set()
    fields = (line.get('ref'), line.get('invoice_number'),
              line.get('claim_number'), line.get('description'))
    for raw in fields:
        text = str(raw or '')
        if not text.strip():
            continue
        for m in _CLAIM_RE.findall(text):
            tokens.add(_norm(m))
        # The suffixed invoice form too ('G2026004567-4546' -> a token distinct
        # from the bare claim), harvested from raw text where the '-' still
        # marks the boundary. Cannot create a NEW clash: any text matching this
        # also matches _CLAIM_RE, so the set of SHARED tokens between two lines
        # is unchanged — this only lets claim_tokens() classify correctly.
        for m in _CLAIM_INVOICE_RE.findall(text):
            tokens.add(_norm(m))
        for m in _NUMERIC_REF_RE.findall(text):
            tokens.add(_norm(m))
    # The explicit reference fields also count whole, so a non-numeric supplier
    # reference (e.g. 'ORANGE JULY 26', 'FMRE-MP MINING JUNE') is still matched.
    for raw in (line.get('ref'), line.get('invoice_number'), line.get('claim_number')):
        norm = _norm(raw)
        if len(norm) >= 4:
            tokens.add(norm)
    return {t for t in tokens if len(t) >= 4}


def claim_tokens(line: dict) -> set[str]:
    """Only the bare Graphite claim numbers on a line — the tokens naming the
    CLAIM, not one of its invoices. Harvested from the RAW text, where the
    boundary between claim and invoice suffix still exists: _CLAIM_RE pulls
    'G2026004567' out of 'G2026004567-4546', never the suffixed whole. (After
    _norm the suffixed form ALSO matches _CLAIM_RE, so this cannot be classified
    on normalised tokens.)"""
    out: set[str] = set()
    for raw in (line.get('ref'), line.get('invoice_number'),
                line.get('claim_number'), line.get('description')):
        text = str(raw or '')
        if not text.strip():
            continue
        for m in _CLAIM_RE.findall(text):
            out.add(_norm(m))
    return {t for t in out if len(t) >= 4}


def _invoice_ids(line: dict, claims: set[str]) -> set[str]:
    """Tokens that actually NAME an invoice on this line: the digits after a
    claim-invoice suffix (claim prefix stripped), any long numeric reference, or
    the invoice_number field itself. Payee text embedded in a ref is NOT an
    invoice id — 'G2026004524 CARFIL SERVICE' names the claim and the payee, not
    a second invoice, so it must never make two copies of that line look
    different (that is the literal 3-Aug CARFIL typo pair). Leading zeros
    stripped so '4546' and '04546' are one invoice."""
    ids: set[str] = set()
    for raw in (line.get('ref'), line.get('invoice_number'),
                line.get('claim_number'), line.get('description')):
        text = str(raw or '')
        for m in _CLAIM_INVOICE_RE.findall(text):
            whole = _norm(m)
            for c in claims:
                if whole.startswith(c) and len(whole) > len(c):
                    ids.add(whole[len(c):])          # just the invoice suffix
                    break
            else:
                ids.add(whole)
        for m in _NUMERIC_REF_RE.findall(text):
            ids.add(_norm(m))
    inv_field = _norm(line.get('invoice_number'))
    if len(inv_field) >= 4:
        ids.add(inv_field)
    return {i.lstrip('0') or '0' for i in ids}


def _different_invoices_of_one_claim(tok_a, ln_a, tok_b, ln_b) -> bool:
    """True when two lines match ONLY on a shared claim number and each names a
    DIFFERENT invoice — two invoices of one claim, not the same invoice twice
    (Pako Kago, 2026-09-02: claim G2026004567, OMEGA AUTOWORLD, invoices -4546
    and -4550, equal amounts).

    One side with no invoice identifier proves nothing and stays hard:
    G2026004524 (3 Aug) was paid with invoice 9553 on the register while the
    pending copies carried none — that duplicate must still block. And a shared
    invoice id (the same invoice written two ways, e.g. ref 'G…-4546' vs
    invoice_number '4546') is the SAME invoice, so it stays hard too."""
    shared = tok_a & tok_b
    if not shared:
        return False
    claims_shared = claim_tokens(ln_a) & claim_tokens(ln_b)
    if not shared <= claims_shared:
        # They share more than the claim — an invoice number, a supplier ref.
        return False
    inv_a = _invoice_ids(ln_a, claims_shared)
    inv_b = _invoice_ids(ln_b, claims_shared)
    if not inv_a or not inv_b:
        return False                    # either side bare — cannot prove they differ
    return not (inv_a & inv_b)          # a shared invoice id = the SAME invoice = hard


def _line_label(line: dict) -> str:
    return (str(line.get('description') or '').strip()
            or str(line.get('ref') or '').strip()
            or str(line.get('claim_number') or '').strip()
            or 'no description')


def _index_lines(lines) -> list[tuple[set[str], Decimal, dict]]:
    """(tokens, amount, line) for each line of a stored request."""
    out = []
    for ln in (lines or []):
        if not isinstance(ln, dict):
            continue
        out.append((line_tokens(ln), _money(ln.get('amount')), ln))
    return out


def compare_lines(lines, existing, *, currency: str, new_period: str = '',
                  new_payee: str = '') -> dict:
    """The whole comparison, with NO database in it.

    `existing` is an iterable of (ref, status, line_items) — or, optionally,
    (ref, status, line_items, period) — for the requests to compare against. The
    caller decides which those are. Keeping this pure is deliberate: the matching
    rules are the part that must be provable, and a rule that can only be
    exercised through a Django test database is a rule that goes unverified
    whenever the database is unavailable.

    `new_period` and each existing `period` are month keys ('YYYY-MM', the month
    the request was raised). They ONLY affect the no-reference branch, and only
    to STOP a false block: a fixed monthly payment (salary, rent, a compliance
    fee — same Rand amount every month, no invoice number) was blocked because
    this month's amount equals last month's paid amount. When both periods are
    known and DIFFERENT, that no-reference amount+payee match is a different
    month's payment, so it drops to a soft warning instead of a hard block. Same
    month, or an unknown period on either side, still blocks exactly as before —
    so a genuine same-month double-pay is untouched. Reference-based clashes (a
    shared claim/invoice number) never look at period.

    Returns {'hard': [...], 'soft': [...]}. `hard` entries are exact
    reference+amount clashes and block; `soft` entries are near misses worth an
    approver's eye and do not block. Each entry names the line, the amount and
    the request it clashes with, so the message tells the raiser exactly which
    row to delete.
    """
    hard: list[dict] = []
    soft: list[dict] = []

    incoming = []
    for i, ln in enumerate(lines or [], 1):
        incoming.append((i, line_tokens(ln), _money(ln.get('amount')), ln))

    # ── 1. The pack against itself ───────────────────────────────────────────
    # PAY/ADIC/2026/07/28/0001 carried G2026005105 twice. A pack that duplicates
    # its own line pays it twice on one authorisation.
    for a in range(len(incoming)):
        i_a, tok_a, amt_a, ln_a = incoming[a]
        for b in range(a + 1, len(incoming)):
            i_b, tok_b, amt_b, ln_b = incoming[b]
            if amt_a != amt_b:
                continue
            if tok_a & tok_b:
                if _different_invoices_of_one_claim(tok_a, ln_a, tok_b, ln_b):
                    # Same claim, same amount, but each a DIFFERENT invoice —
                    # legitimate (a claim carries several invoices). Not blocked;
                    # surfaced so the approver still eyeballs it.
                    soft.append({
                        'line': i_b,
                        'label': _line_label(ln_b),
                        'amount': str(amt_b),
                        'clash_kind': 'same_claim_other_invoice',
                        'clash_ref': '',
                        'clash_status': '',
                        'detail': (f'line {i_b} is on the same claim as line '
                                   f'{i_a} for the same amount '
                                   f'({currency} {amt_a:,.2f}) but under a '
                                   f'different invoice number — not blocked; '
                                   f'confirm both invoices are genuinely unpaid'),
                    })
                    continue
                pass
            elif (not tok_a and not tok_b
                  and _norm(_line_label(ln_a)) == _norm(_line_label(ln_b))
                  and len(_norm(_line_label(ln_a))) >= 4):
                # Neither line carries a reference we can tokenise, so the rule
                # above never compared them. 'FAC Premium – May' with claim 'N/A'
                # normalises to two characters and is dropped, so 86,470.94 sat
                # twice on PAY/ADIC/2026/08/06/0003 and was paid twice (Manus
                # 2026-08-09). Identical text and identical amount, twice in one
                # pack, is a duplicate on its face.
                #
                # NOT widened to the payee root. Matching 'FAC Premium - May'
                # against '- June' would block PAY 'FAC Premium Cessions', a pack
                # of real data where FMRE-MP MINING May and June are both
                # 43,805.74 and Continental RE APR-MAY and JUNE-JUL are both
                # 25,573.00 — different periods, genuine payments. Same payee,
                # same amount, different period is NORMAL in facultative
                # cessions, so no rule here can separate the good from the bad.
                # That one is a Finance judgement (raised 2026-08-10).
                pass
            else:
                continue
            hard.append({
                'line': i_b,
                'label': _line_label(ln_b),
                'amount': str(amt_b),
                'clash_kind': 'same_request',
                'clash_ref': '',
                'clash_status': '',
                'detail': (f'line {i_b} repeats line {i_a} on this same request '
                           f'({_line_label(ln_a)}, {currency} {amt_a:,.2f})'),
            })

    # ── 2. The pack against the register ─────────────────────────────────────
    for ex_row in existing:
        ex_ref, ex_status, ex_lines = ex_row[0], ex_row[1], ex_row[2]
        ex_period = ex_row[3] if len(ex_row) > 3 else ''
        ex_payee = ex_row[4] if len(ex_row) > 4 else ''
        for tok_e, amt_e, ln_e in _index_lines(ex_lines):
            for i, tok_n, amt_n, ln_n in incoming:
                # Same payee, same amount, no shared reference. Six of the eleven
                # groups paid twice carried no claim number at all — CFAO
                # MOBILITY 104,184.87, Unicoin rent 24,504.94, FAC Premium
                # 86,470.94 — and the token rule below skipped them outright,
                # so they were never compared against the register (Manus
                # 2026-08-09). The text carries the period, so a genuine monthly
                # payment ('Rent July 26' vs 'Rent August 26') still differs.
                if not (tok_n and tok_e):
                    lbl_n, lbl_e = _norm(_line_label(ln_n)), _norm(_line_label(ln_e))
                    if amt_n == amt_e and lbl_n == lbl_e and len(lbl_n) >= 4:
                        # A fixed monthly payment repeats the same amount AND the
                        # same payee text every month — 'Salary 5,557.91',
                        # 'Omega Compliance 3,409.75' (Laone Thebe, SA operations,
                        # 25 Aug 2026). When the request was raised in a different
                        # month from the one already on file, it is this month's
                        # payment, not a repeat, so it must not be blocked. Same
                        # month, or an unknown month on either side, still blocks.
                        different_month = bool(new_period and ex_period
                                               and new_period != ex_period)
                        (soft if different_month else hard).append({
                            'line': i,
                            'label': _line_label(ln_n),
                            'amount': str(amt_n),
                            'clash_kind': ('recurring_other_month' if different_month
                                           else 'existing_request'),
                            'clash_ref': ex_ref,
                            'clash_status': ex_status,
                            'detail': (
                                (f'{currency} {amt_n:,.2f} to the same payee '
                                 f'({_line_label(ln_n)}) is also on {ex_ref} '
                                 f'({_status_word(ex_status)}), but that was a '
                                 f'different month ({ex_period}) — a fixed monthly '
                                 f'amount, not blocked')
                                if different_month else
                                (f'{currency} {amt_n:,.2f} to the same payee '
                                 f'({_line_label(ln_n)}) is already on '
                                 f'{ex_ref} ({_status_word(ex_status)}) — '
                                 f'neither line carries a reference number')),
                        })
                    continue
                shared = tok_n & tok_e
                if shared and amt_n == amt_e:
                    if _different_invoices_of_one_claim(tok_n, ln_n, tok_e, ln_e):
                        # A different invoice of a claim whose earlier invoice is
                        # already on the register at the same amount — legitimate
                        # (Pako Kago 2026-09-02). Not blocked; shown as a warning.
                        soft.append({
                            'line': i,
                            'label': _line_label(ln_n),
                            'amount': str(amt_n),
                            'clash_kind': 'same_claim_other_invoice',
                            'clash_ref': ex_ref,
                            'clash_status': ex_status,
                            'detail': (f'{sorted(shared)[0]} also appears on '
                                       f'{ex_ref} ({_status_word(ex_status)}) at '
                                       f'the same amount {currency} {amt_e:,.2f}, '
                                       f'but under a different invoice number — '
                                       f'not blocked; confirm this invoice is '
                                       f'genuinely unpaid'),
                        })
                        continue
                    # One claim number legitimately pays several DIFFERENT
                    # suppliers — the windscreen supplier, the engine supplier, the
                    # assessment report — each on its own supplier payment request
                    # (Leano Makwapa, 2026-09-02). When two requests share ONLY the
                    # claim number, at the same amount, but go to genuinely
                    # different payees, that is not a repeat. Every historical true
                    # double-pay (P399k, 3 Aug) was the SAME payee, so a real payee
                    # difference clears it. Same payee, or a shared invoice/voucher
                    # number (shared not <= claims), still blocks below. Kept soft,
                    # not silent: the approver still eyeballs it, and money never
                    # leaves Omni — the bank 2FA is the real gate.
                    claims_shared = claim_tokens(ln_n) & claim_tokens(ln_e)
                    if (claims_shared and shared <= claims_shared
                            and _norm(new_payee) and _norm(ex_payee)
                            and _norm(new_payee) != _norm(ex_payee)):
                        soft.append({
                            'line': i,
                            'label': _line_label(ln_n),
                            'amount': str(amt_n),
                            'clash_kind': 'same_claim_other_payee',
                            'clash_ref': ex_ref,
                            'clash_status': ex_status,
                            'detail': (f'{sorted(shared)[0]} also appears on '
                                       f'{ex_ref} ({_status_word(ex_status)}) at '
                                       f'the same amount {currency} {amt_e:,.2f}, '
                                       f'but that pays a different supplier '
                                       f'({ex_payee or "unnamed"}) — one claim can '
                                       f'pay several suppliers; not blocked, '
                                       f'confirm this supplier is genuinely unpaid'),
                        })
                        continue
                    hard.append({
                        'line': i,
                        'label': _line_label(ln_n),
                        'amount': str(amt_n),
                        'clash_kind': 'existing_request',
                        'clash_ref': ex_ref,
                        'clash_status': ex_status,
                        'detail': (f'{currency} {amt_n:,.2f} against '
                                   f'{sorted(shared)[0]} is already on {ex_ref} '
                                   f'({_status_word(ex_status)})'),
                    })
                elif shared:
                    soft.append({
                        'line': i,
                        'label': _line_label(ln_n),
                        'amount': str(amt_n),
                        'clash_kind': 'same_ref_other_amount',
                        'clash_ref': ex_ref,
                        'clash_status': ex_status,
                        'detail': (f'{sorted(shared)[0]} also appears on {ex_ref} '
                                   f'({_status_word(ex_status)}) at '
                                   f'{currency} {amt_e:,.2f}'),
                    })

    return {'hard': _dedupe(hard), 'soft': _dedupe(soft)[:20]}


def find_duplicates(lines, *, currency: str, exclude_pk=None,
                    entity: str = '', payee: str = '') -> dict:
    """compare_lines() against every live and paid request in the register.

    Currency is part of the comparison: BWP 1,000 and ZAR 1,000 are not the same
    payment. Entity is NOT — the 3 Aug duplicates crossed 'Alpha Direct
    Insurance' and 'Alpha Direct Insurance Company', which are the same company
    typed two ways, and keying on it would have let every one of them through.
    """
    from django.utils import timezone

    from .models import PaymentRequest

    qs = (exclude_dead(PaymentRequest.objects.filter(currency=currency))
          # Drafts (CFO handover 2026-09-02) are pre-submission WIP — not raised
          # requests, so they never count as a duplicate (and a draft would else
          # flag itself the moment it is submitted).
          .exclude(status=PaymentRequest.Status.DRAFT)
          .only('id', 'ref', 'status', 'line_items', 'currency', 'created_at', 'payee'))
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    # The month a request was raised is what separates one month's fixed payment
    # from the next when the line carries no invoice number. Anchor on when it
    # was raised (created_at), the same clock for both sides, so 'Salary' in
    # August never reads as a repeat of 'Salary' in July.
    new_period = timezone.localtime(timezone.now()).strftime('%Y-%m')
    existing = ((pr.ref, pr.status, pr.line_items or [],
                 timezone.localtime(pr.created_at).strftime('%Y-%m') if pr.created_at else '',
                 pr.payee or '')
                for pr in qs.iterator())
    return compare_lines(lines, existing, currency=currency, new_period=new_period,
                         new_payee=payee)


def _status_word(status: str) -> str:
    """A status in words a reader can act on.

    The fallback returns the raw enum, which is how 'cancelled' reached the
    committee's email reading "already raised or paid … (cancelled)" — the
    headline and the evidence under it contradicting each other in the same
    message (21-Sep-2026).
    """
    return {
        'pending_finance': 'awaiting finance sign-off',
        'pending_cfo':     'awaiting CFO authorisation',
        'paid':            'already paid',
        'cancelled':       'cleared without being paid',
        'rejected':        'rejected, never paid',
        'draft':           'still a draft',
        'exception':       'with the exception committee',
    }.get(status, status)


def all_clashes_never_paid(hard: list[dict]) -> bool:
    """True when every hard clash is against a CLOSED request the bank never
    paid — the case created on 21-Sep-2026 when sixteen bank-rejected requests
    were cleared on the CFO's instruction.

    It reads differently again from both the twin-pack and the already-paid
    case, and getting it wrong is expensive in the one direction that matters.
    The money did NOT move, so the supplier may legitimately need paying — but
    they may equally have been paid by hand outside Omni after the bank threw
    the instruction out, which is exactly how the same invoice gets paid twice.
    """
    if not hard:
        return False
    return all(r.get('clash_kind') == 'existing_request'
               and r.get('clash_status') in DEAD_STATUSES
               for r in hard)


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        key = (r['line'], r['clash_kind'], r['clash_ref'], r['amount'])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return sorted(out, key=lambda r: (r['line'], r['clash_ref']))


def _clashing_refs(hard: list[dict]) -> list[str]:
    """The other requests named in a set of hard clashes, in order, deduped."""
    out: list[str] = []
    for r in hard:
        ref = (r.get('clash_ref') or '').strip()
        if ref and ref not in out:
            out.append(ref)
    return out


def all_clashes_unpaid(hard: list[dict]) -> bool:
    """True when every hard clash is against a request that has NOT been paid.

    This is the twin-pack case and it reads completely differently to the raiser
    or approver: nothing has left the bank, the same line is simply sitting on two
    live requests at once. Saying "already paid" there sends someone hunting a
    payment that was never made, and — worse — implies the request in front of
    them is the wrong one, when either twin may be the keeper.

    A clash inside the SAME request ('same_request') is not an unpaid twin: the
    pack duplicates its own line and the fix is to delete a row, so it must never
    put the message into twin mode.
    """
    if not hard:
        return False
    return all(r.get('clash_kind') == 'existing_request'
               and r.get('clash_status') in ('pending_finance', 'pending_cfo')
               for r in hard)


def blocking_message(hard: list[dict], *, currency: str, total_hard: Decimal,
                     context: str = 'raise') -> str:
    """The refusal the reader acts on. Names every clashing line, because a
    generic 'duplicate detected' just gets retried.

    `context` decides the closing instruction, because the two readers can do
    completely different things:

      'raise'     — the person building the pack. They own the lines, so the
                    answer is delete the row, and the override box sits right
                    there under the red panel in the new-payment form.
      'authorise' — a finance approver or the CFO, reading a pack somebody else
                    raised, from a task in their inbox. They CANNOT edit its
                    lines and there is no override box on that screen. Telling
                    them to "remove those lines and write why in the box below"
                    (CFO, 3 Aug 2026, blocked on PAY/ADIC/2026/07/23/0001) points
                    at a door that does not exist on the page they are standing
                    on, so the control reads as a bug instead of a finding.
    """
    unpaid_twins = all_clashes_unpaid(hard)
    never_paid = all_clashes_never_paid(hard)
    n = len(hard)
    if never_paid:
        head = (f'This payment repeats {n} line{"" if n == 1 else "s"} — '
                f'{currency} {total_hard:,.2f} — from an earlier request that '
                f'the BANK REJECTED and that has since been cleared. '
                f'No money moved on it, so this may genuinely still be owed. '
                f'Before paying it again, confirm the supplier was not already '
                f'paid by hand outside Omni after the bank threw the first '
                f'attempt out.')
    elif unpaid_twins:
        head = (f'This payment repeats {n} line{"" if n == 1 else "s"} — '
                f'{currency} {total_hard:,.2f} — that {"is" if n == 1 else "are"} '
                f'already sitting on ANOTHER LIVE REQUEST. '
                f'Nothing has been paid yet: the same line is queued twice, so '
                f'authorising both would pay it twice.')
    else:
        head = (f'This payment repeats {n} '
                f'line{"" if n == 1 else "s"} that '
                f'{"is" if n == 1 else "are"} already raised or paid — '
                f'{currency} {total_hard:,.2f} of it.')
    rows = [f'  • Line {r["line"]} ({r["label"]}): {r["detail"]}.' for r in hard[:12]]
    if n > 12:
        rows.append(f'  • …and {n - 12} more.')

    if context == 'authorise':
        refs = _clashing_refs(hard)
        others = (' and '.join(refs) if len(refs) < 3
                  else ', '.join(refs[:-1]) + ' and ' + refs[-1])
        if never_paid:
            tail = (f'You cannot edit this pack from here, and there is nothing '
                    f'to clear — {others} {"was" if len(refs) == 1 else "were"} '
                    f'already cleared without being paid. Check the bank for a '
                    f'manual payment to this supplier. If none was made, '
                    f'authorise this one; if one was, send it back so it can be '
                    f'withdrawn.')
        elif unpaid_twins and others:
            tail = (f'You cannot edit this pack from here, and there is nothing to '
                    f'pay twice yet — one of these requests has to go. Open '
                    f'Payment Requests, decide which of this request and '
                    f'{others} is the duplicate, and use "Clear from queue" on '
                    f'that one (a written reason is required). Then authorise the '
                    f'one you kept.')
        else:
            tail = ('You cannot edit this pack from here. Either use "Clear from '
                    'queue" on Payment Requests to kill it, or send it back to '
                    'the person who raised it to drop the repeated lines and '
                    're-raise the rest. If it genuinely must go out again — the '
                    'bank rejected the first attempt — they record why when they '
                    're-raise it, and that reason is printed on the pack.')
    else:
        tail = ('Remove those lines and submit the rest. If this genuinely must be '
                'paid again — for example the bank rejected the first attempt — '
                'write why in the box below and submit again. Your reason is stored '
                'on the request and printed on the authorisation pack.')
    return '\n'.join([head, '', *rows, '', tail])


def hard_total(hard: list[dict]) -> Decimal:
    return sum((_money(r['amount']) for r in hard), Decimal('0.00'))
