"""underwriting/quote_parse.py — plain English → a drafted quotation.

The underwriter types what they have, in whatever words they use on the phone:

    "Northbound Haulage, fleet of 14 — 6 heavy trucks 8.4m, 8 bakkies 3.12m, both
     retail value, excess 15k heavy 7.5k light. TP liability 5m. Add passenger
     liability 250k and SADC transit. Broker Redline. Premium 418,600."

...and gets the standard quotation back, filled in and ready to check. Same idea
as the ask box on payment requests.

WHAT THE MODEL IS ALLOWED TO DO
    Read the sentence and lay it out: client, class, period, broker, and the
    cover rows (section, sum insured, basis, excess).

WHAT THE MODEL IS NOT ALLOWED TO DO — and this is the whole point
    Touch the money. VAT and the total are NEVER model output; they are computed
    here from the one rule the CFO signed off (premium + 14% = total). The reason
    the loose Excel quotes had VAT three different ways is that the arithmetic
    lived wherever the person put it. A language model is the last thing that
    should own it.

    It MAY suggest a premium when the underwriter typed none (CFO 2026-08-08),
    but the suggestion comes back flagged `premium_is_suggested` and the quote
    REFUSES to issue while that flag is set. The underwriter has to look at the
    number and confirm it. That keeps the speed the CFO asked for without a
    guessed figure ever reaching a broker unread.

    It cannot invent a client. If no client name is found, the draft comes back
    without one and the screen asks — better a blank than the wrong name on
    someone else's quote, which is exactly how one client's file became another's.

SAFETY
    Calls go through core.ai_assist.deepseek_complete, which already runs the PII
    firewall over the prompt, falls back to the backup engine when DeepSeek is
    switched off, and resolves the key from the vault. Nothing new is opened up
    here. A client's name IS customer data, so it is masked on the way out by that
    firewall — the model sees the shape of the request, not the customer.

    If the engine is unavailable, a deterministic parser runs so the box still
    works. It reads figures and obvious sections; it is not clever, but it never
    leaves the underwriter stuck in front of a dead text box.

    User-facing name is "Aria" — never "DeepSeek" (CFO 2026-06-29).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# The one VAT rule. Signed off by the CFO 2026-08-08. One place, one number.
VAT_RATE = Decimal('0.14')

_SYSTEM = """You lay out insurance quotations for a Botswana short-term insurer.

Read the underwriter's note and return ONLY a JSON object:

{
  "client_name":  "",            // exactly as written; "" if not stated
  "client_attn":  "",            // contact/address line if stated
  "class_of_business": "",       // e.g. "Motor Fleet", "Group Health", "Fire & Perils"
  "period":       "12 months",
  "broker":       "",
  "premium":      "",            // the premium IN the note, if there is one
  "premium_suggested": "",       // only if the note has NO premium: your best
                                 // estimate for this risk, else ""
  "premium_basis": "",           // one short line saying how you arrived at it
  "sections": [
    {"group": "", "name": "", "note": "",
     "sum_insured": "", "basis": "", "excess": ""}
  ]
}

Rules:
- Copy figures exactly as written. Do not compute anything.
- Do NOT output VAT or a total. They are calculated elsewhere.
- Do NOT invent a client or cover that is not in the note.
- If the note states a premium, put it in "premium" and leave "premium_suggested" empty.
- If it does not, leave "premium" empty and put an estimate in "premium_suggested"
  with one line in "premium_basis". Never put an estimate in "premium".
- "group" buckets rows under a heading (e.g. "Motor Fleet - 14 vehicles",
  "Extensions"). Leave "" if there is no grouping.
- basis is what the sum insured is measured on: "Retail value", "Limit",
  "Reinstatement", "As per policy".
- Where cover is included rather than valued, put "Included" in sum_insured.
- NEVER output reinsurance: no facultative or treaty placement, no reinsurer
  names, no retention, cession, quota share or excess-of-loss detail, even when
  the note states it. A quotation is a client document; how the risk is laid off
  is not the client's business. Keep the cover the client is buying, drop the
  arrangement behind it.
- Return the JSON and nothing else."""


# ── money ────────────────────────────────────────────────────────────────────

def _to_decimal(raw) -> Decimal | None:
    """'418,600' / '8.4m' / 'P 15 000' → Decimal. None if it isn't a number."""
    if raw is None:
        return None
    s = str(raw).strip().lower().replace('bwp', '').replace('p', '', 1)
    s = s.replace(',', '').replace(' ', '')
    if not s:
        return None
    mult = 1
    if s.endswith('m'):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith('k'):
        mult, s = 1_000, s[:-1]
    try:
        return (Decimal(s) * mult).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def price(premium) -> dict:
    """The locked money block: premium → VAT → total. Computed here, never by AI.

    Returns zeros rather than raising, so a draft with no premium still renders
    and the underwriter simply types the figure in.
    """
    net = _to_decimal(premium) or Decimal('0.00')
    # Half UP, not Python's default. Decimal rounds half to EVEN, so 0.125 went
    # DOWN to 0.12 while a broker checking the sum by hand gets 0.13 — the
    # quotation and the person reading it disagreed by a thebe. Tax rounds half
    # up (CFO 2026-08-09).
    vat = (net * VAT_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {
        'premium': net,
        'vat': vat,
        'vat_rate_pct': str((VAT_RATE * 100).normalize()),
        # Sum the two figures SHOWN, so premium + VAT always equals the total on
        # the page. Re-deriving it from net * 1.14 can differ by a thebe.
        'total': (net + vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
    }


def total_sum_insured(sections) -> Decimal:
    """Sum the numeric sum-insured figures across the cover rows.

    Rows whose sum insured is not a real money figure — 'Included', a limit
    written in words, a blank — are skipped: you cannot rate what has no value.
    Each product keeps its own sum insured and its own excess; this just adds up
    the values a percentage rate can be applied to.
    """
    tot = Decimal('0')
    for s in (sections or []):
        if not isinstance(s, dict):
            continue
        v = _to_decimal(s.get('sum_insured'))
        if v and v > 0:
            tot += v
    return tot


def price_from_rate(total_si, rate_pct, incl_vat) -> dict:
    """Premium from a RATE on the sum insured — same locked money block as price().

    rate_pct is a percentage (3 => 3%). SI x rate gives the figure the underwriter
    means by "3% of a million".
      - incl_vat=True  : that figure already carries VAT, so it IS the total; the
                         net premium is backed out (total / 1.14) and VAT is the
                         difference, so premium + VAT always equals the total.
      - incl_vat=False : that figure is the net premium; VAT is added on top,
                         exactly as price() does.
    HALF UP throughout (CFO 2026-08-09). Returns zeros rather than raising.
    """
    si = _to_decimal(total_si) or Decimal('0.00')
    rate = _to_decimal(rate_pct) or Decimal('0.00')
    figure = (si * rate / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return _money_from_figure(figure, incl_vat)


def _money_from_figure(figure: Decimal, incl_vat: bool) -> dict:
    """A computed premium figure → the locked {premium, vat, total} block.

    incl_vat=True: `figure` is the GROSS total, net is backed out (figure / 1.14)
    and VAT is the difference, so premium + VAT == total exactly. incl_vat=False:
    `figure` is the net premium and VAT is added, exactly as price(). One place.
    """
    if not incl_vat:
        return price(figure)
    total = figure
    net = (total / (Decimal('1') + VAT_RATE)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    vat = (total - net).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {
        'premium': net,
        'vat': vat,
        'vat_rate_pct': str((VAT_RATE * 100).normalize()),
        'total': total,
    }


def unrated_covered_rows(sections):
    """When per-product rating is in use (at least one row has a rate), a row with
    a numeric sum insured but NO rate is missing from the premium — that product
    would be given away free. Returns those rows' names so the quote can refuse to
    issue; empty list when the quote is not section-rated. Rows with no numeric
    sum insured ('Included', limits, blank) don't need a rate and are exempt.

    ONE exemption, and it is deliberately narrow: a row sitting under a NAMED
    section that is ALREADY PRICED is a sub-limit of that section, not a product
    of its own. Public liability is rated once and then lists its products
    liability, defective workmanship, legal defence and wrongful arrest limits;
    workmen's compensation is rated on the wage roll and then states the common
    law limit. Those limits are what the section's premium buys, and requiring a
    rate on each of them blocked a correctly-priced quotation from being issued
    at all (Q-2026-00009, CFO 2026-08-11).

    The exemption needs a real section heading. A row with no heading above it is
    in nobody's section, so it still blocks — otherwise one rated row in a flat
    ungrouped list would excuse every unrated row after it and the guard would
    stop guarding anything.
    """
    rows, rated_any, last = [], False, ''
    for s in (sections or []):
        if not isinstance(s, dict):
            continue
        label = (s.get('group') or '').strip()
        if label:
            last = label
        si = _to_decimal(s.get('sum_insured'))
        rt = _to_decimal(s.get('rate'))
        pr = _to_decimal(s.get('premium'))
        # Priced = a rate OR a flat typed premium on the row.
        priced = bool((rt and rt > 0) or (pr and pr > 0))
        rated_any = rated_any or priced
        rows.append({'section': last, 'priced': priced, 'has_sum': bool(si and si > 0),
                     'name': s.get('name') or 'a cover row'})
    if not rated_any:
        return []
    priced_sections = {r['section'] for r in rows if r['priced'] and r['section']}
    return [r['name'] for r in rows
            if not r['priced'] and r['has_sum'] and r['section'] not in priced_sections]


def section_premium_rows(sections, incl_vat):
    """Per-row NET premiums, group subtotals, and the grand net — the client-facing
    breakdown, and the ONE place a per-row premium is ever computed.

    A row is priced by either:
      - a typed `premium` on the row — a flat premium (Money, GIT, fees), or
      - `sum_insured` x `rate` %.
    A typed premium wins over the rate for that row.

    When incl_vat=True the row figure CARRIES VAT, so each row's net is backed out
    of it (figure / 1.14). Each row's net is rounded to the thebe INDIVIDUALLY and
    the grand net is the literal sum of those printed figures — so what the client
    adds up on the page is exactly the net premium we charge. Rounding once at the
    end instead would leave the printed rows disagreeing with the printed total by
    a thebe or two, which is precisely the disease this module exists to end.

    Returns None when no row is priced (caller falls back to a quote-level rate or
    the typed premium). Otherwise:
      {'rows': [{'index', 'net'}], 'groups': [{'group', 'net'}], 'net': Decimal}
    `groups` carries ONE entry per section name, in the order the sections first
    appear, even when that section's rows are scattered through the schedule.
    """
    priced = []
    total_figure = Decimal('0')
    for i, s in enumerate(sections or []):
        if not isinstance(s, dict):
            continue
        figure = _to_decimal(s.get('premium'))
        if figure is None or figure <= 0:
            si = _to_decimal(s.get('sum_insured'))
            rate = _to_decimal(s.get('rate'))
            figure = (si * rate / Decimal('100')) if (si and si > 0 and rate and rate > 0) else None
        if figure is None or figure <= 0:
            continue
        figure = figure.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        total_figure += figure
        if incl_vat:
            net = (figure / (Decimal('1') + VAT_RATE)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            net = figure
        priced.append({'index': i, 'net': net, 'group': (s.get('group') or '').strip()})
    if not priced:
        return None

    # When incl_vat=True the row figure IS the gross the client sees on that line
    # (SI x rate) — the SAME figure the "system-calculated premium" tile on the
    # quote screen shows. Grand gross is the SUM of those figures, and net is
    # backed out of the gross ONCE. Doing this the other way — summing per-row
    # nets and then forward-adding VAT — drifted by a thebe on figures that do
    # not divide by 1.14 cleanly (Gomolemo, ADIC 2026-08-19: 260,000 x 3.2%% =
    # 8,320.00 on screen, 8,320.01 on the PDF).
    #
    # Per-row nets are still each rounded to the thebe. Any rounding residual
    # between the grand net and the sum of the printed row nets is absorbed onto
    # the LAST priced row, so what the client adds up in the breakdown still
    # equals the printed net premium exactly.
    if incl_vat:
        grand_net = (total_figure / (Decimal('1') + VAT_RATE)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP)
        residual = grand_net - sum((p['net'] for p in priced), Decimal('0'))
        if residual:
            priced[-1]['net'] += residual

    # A row inherits the last group heading above it, exactly as the table reads.
    last_seen = ''
    for i, s in enumerate(sections or []):
        if isinstance(s, dict) and (s.get('group') or '').strip():
            last_seen = (s.get('group') or '').strip()
        for p in priced:
            if p['index'] == i:
                p['group_label'] = last_seen

    # ONE subtotal per SECTION NAME, not one per run of rows. A schedule that
    # came back to Fire after Motor printed "Fire" twice with half the money in
    # each, and the summary of premiums by section then carried two Fire lines
    # (CFO 2026-08-11, LUCIENT quote). The same label is the same section
    # wherever its rows happen to sit; sections keep the order they first
    # appear, and the subtotals still sum to the net exactly.
    totals: dict[str, Decimal] = {}
    for p in priced:
        label = p.get('group_label') or ''
        totals[label] = totals.get(label, Decimal('0')) + p['net']
    groups = [{'group': label, 'net': net} for label, net in totals.items()]

    return {
        'rows': priced,
        'groups': groups,
        'net': sum((p['net'] for p in priced), Decimal('0')),
        'total': total_figure if incl_vat else None,
    }


def premium_from_section_rates(sections, incl_vat):
    """The {premium, vat, total} block for a quote priced row-by-row.

    Net = the SUM OF THE PRINTED ROWS (see section_premium_rows), then VAT once on
    that net, so every figure on the document reconciles to the thebe. Returns None
    when no row is priced.
    """
    b = section_premium_rows(sections, incl_vat)
    if b is None:
        return None
    if incl_vat:
        # Gross the client sees on each line IS the total: back out net once, so
        # the grand total on the PDF equals the sum of SI x rate figures to the
        # thebe (Gomolemo, ADIC 2026-08-19).
        return _money_from_figure(b['total'], incl_vat=True)
    return price(b['net'])          # net -> +14% -> total, the one rule


def premium_appears_in(premium, text: str) -> bool:
    """Did this premium actually come from the underwriter's note?

    Compared as NUMBERS, one token at a time — not as a run of digits. Stripping
    every non-digit and asking whether the premium is a substring looks right and
    is not: "fleet of 14 vehicles, contents sum 186,000" becomes "14186000",
    which contains "418600", so a premium Aria invented reads as one the
    underwriter typed. Fable proved that exact case, and it defeats the only gate
    standing between a guessed premium and a broker.

    Each number in the note is parsed on its own and compared to the premium, so
    "418.6k", "418,600" and "0.4186m" all match 418600 and nothing matches across
    a boundary.
    """
    want = _to_decimal(premium)
    if want is None or want <= 0 or not text:
        return False
    for token in re.findall(r'\d[\d,\. ]*\s*[mMkK]?', text):
        got = _to_decimal(token.strip())
        if got is not None and got == want:
            return True
    return False


# ── deterministic fallback ───────────────────────────────────────────────────

_SECTION_HINTS = [
    (r'comprehensive', 'Comprehensive', 'Retail value'),
    (r'third[- ]?party|tp liability', 'Third-party liability', 'Limit'),
    (r'passenger', 'Passenger liability', 'Limit'),
    (r'fire', 'Fire and allied perils', 'Reinstatement'),
    (r'theft|burglary', 'Theft', 'Limit'),
    (r'transit|sadc', 'Territorial limits', 'As per policy'),
    (r'riot|strike', 'Riot, strike and civil commotion', 'As per policy'),
]


# A sum insured has to LOOK like money. Rendering the screen caught the reason:
# "fire and perils on the warehouse at Plot 5512 Gaborone. Buildings 12m..." put
# **5512 — the plot number** in the sum-insured column of a quotation bound for a
# broker. The reader takes the first number after the section keyword, and a plot
# number is a number. So only a token carrying a money marker (m, k, a thousands
# comma, or a decimal) counts; a bare integer is left blank for the underwriter to
# fill. Blank asks a question. A wrong figure does not.
_MONEY_NEAR = re.compile(r'(\d[\d,\. ]*\s*[mMkK]|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)')


def _money_near(fragment: str) -> str:
    m = _MONEY_NEAR.search(fragment or '')
    return _tidy(m.group(1)) if m else ''


def _tidy(raw: str) -> str:
    """Drop the sentence's punctuation: 'Premium 178,250.' captures the full stop
    too, and '178,250.' is what then sits in the premium box on a quotation."""
    return (raw or '').strip().rstrip('.,;: ').strip()


def _fallback(text: str) -> dict:
    """No engine: pull out what can be read without cleverness."""
    t = text or ''
    sections = []
    for pattern, name, basis in _SECTION_HINTS:
        m = re.search(pattern, t, re.I)
        if not m:
            continue
        window = t[m.start():m.start() + 120]
        amt = _money_near(window[len(m.group(0)):])
        sections.append({
            'group': '', 'name': name, 'note': '',
            'sum_insured': amt,
            'basis': basis, 'excess': '',
        })
    prem = re.search(r'premium[^\d]{0,12}([\d][\d,\. ]*\s*[mk]?)', t, re.I)
    broker = re.search(r'broker[:\s]+([A-Z][\w &\'-]{2,40})', t, re.I)
    return {
        'client_name': '', 'client_attn': '',
        'class_of_business': '', 'period': '12 months',
        'broker': _tidy(broker.group(1)) if broker else '',
        'premium': (_tidy(prem.group(1)) if prem else ''),
        'sections': sections,
    }


# ── public ───────────────────────────────────────────────────────────────────

def draft_quote(text: str) -> dict:
    """Turn the underwriter's note into a drafted quotation.

    Returns {ok, source, draft, money, warnings}. Never raises: the box must
    always give something back.
    """
    text = (text or '').strip()
    if not text:
        return {'ok': False, 'source': 'none', 'draft': _fallback(''),
                'money': price(None), 'premium_is_suggested': False,
                'premium_basis': '', 'warnings': ['Nothing typed yet.']}

    source, parsed = 'Aria', None
    try:
        from core.ai_assist import deepseek_complete, DeepSeekUnavailable
        try:
            raw = deepseek_complete(text, system_prompt=_SYSTEM,
                                    response_format='json_object', max_tokens=1200)
            parsed = json.loads(raw)
        except (DeepSeekUnavailable, ValueError, json.JSONDecodeError):
            parsed = None
    except BaseException:                       # noqa: BLE001 — never 500 the screen
        parsed = None

    if not isinstance(parsed, dict):
        source, parsed = 'Typed reading (Aria unavailable)', _fallback(text)

    warnings = []

    # A premium in the "premium" field must genuinely appear in the note. If the
    # model put an estimate there instead of in premium_suggested, demote it —
    # a guess must never be indistinguishable from a figure the underwriter gave.
    prem = str(parsed.get('premium') or '').strip()
    suggested = str(parsed.get('premium_suggested') or '').strip()
    basis = str(parsed.get('premium_basis') or '').strip()
    is_suggested = False

    if prem and not premium_appears_in(prem, text):
        suggested, prem = prem, ''          # demote: it was not in the note

    if not prem and suggested:
        prem, is_suggested = suggested, True
        warnings.append(
            'This premium is Aria\'s estimate, not a figure from your note'
            + (f' ({basis})' if basis else '')
            + '. Check it and confirm — the quotation will not issue until you do.')
    elif not prem:
        warnings.append('No premium found. Type it and the VAT and total will follow.')

    if not str(parsed.get('client_name') or '').strip():
        warnings.append('No client name found. Add it before this quotation is issued.')

    sections = [s for s in (parsed.get('sections') or []) if isinstance(s, dict)]
    if not sections:
        warnings.append('No cover sections were recognised — add them by hand.')

    draft = {
        'client_name': str(parsed.get('client_name') or '').strip(),
        'client_attn': str(parsed.get('client_attn') or '').strip(),
        'class_of_business': str(parsed.get('class_of_business') or '').strip(),
        'period': str(parsed.get('period') or '12 months').strip(),
        'broker': str(parsed.get('broker') or '').strip(),
        'sections': sections,
    }
    return {'ok': True, 'source': source, 'draft': draft,
            'money': price(prem), 'premium_is_suggested': is_suggested,
            'premium_basis': basis if is_suggested else '',
            'warnings': warnings}


# The monthly-payment plan. A client may pay the annual premium in 12 monthly
# instalments instead of one sum; paying by instalment carries a charge (CFO
# 2026-08-10). Held here with the rest of the money rules, never in a template.
INSTALMENT_MONTHS = 12
PAYMENT_PLAN_PCT = Decimal('8')          # % added to each monthly instalment (CFO 2026-08-10)


def instalment_plan(total, months=INSTALMENT_MONTHS, charge_pct=PAYMENT_PLAN_PCT) -> dict:
    """Annual total -> the monthly-payment option, shown beside it on the quote.

        monthly premium   = total / 12
        payment plan charge = charge_pct% OF THAT monthly premium
        monthly instalment = monthly premium + charge

    So paying monthly costs charge_pct% more over the year than paying once, and
    every figure printed is the figure the client is actually debited.

    HALF UP on each printed figure, and the 12-month total is the instalment
    times 12 — so what the client multiplies on the page is what they pay. Any
    rounding difference against total x 1.09 lands in that stated total, never in
    a silent extra thebe on one instalment. Returns None for a zero total.
    """
    net = _to_decimal(total) or Decimal('0.00')
    if net <= 0:
        return None
    q = Decimal('0.01')
    monthly = (net / Decimal(months)).quantize(q, rounding=ROUND_HALF_UP)
    charge = (monthly * charge_pct / Decimal('100')).quantize(q, rounding=ROUND_HALF_UP)
    instalment = (monthly + charge).quantize(q, rounding=ROUND_HALF_UP)
    return {
        'months': months,
        'charge_pct': str(charge_pct.normalize()) if isinstance(charge_pct, Decimal) else str(charge_pct),
        'annual_total': net,
        'monthly_premium': monthly,
        'plan_charge': charge,
        'monthly_instalment': instalment,
        'total_over_term': (instalment * Decimal(months)).quantize(q, rounding=ROUND_HALF_UP),
    }


def prorate(annual, months) -> dict | None:
    """A part-year policy: the annual premium charged for the months actually on
    cover (CFO 2026-08-11).

        charged = annual x months / 12

    Twelve months (or anything falsy) means a normal annual policy and returns
    None, so the document says nothing about pro-rating. HALF UP, and the figure
    returned is the NET premium for the shorter period — VAT is applied to it by
    price() exactly as it is to a full year, so there is still one VAT rule.

    Months, not days: the underwriter thinks in months ("six months only") and a
    day count on a quotation invites an argument about which days. The policy
    schedule handles day-level pro-rata at inception.
    """
    net = _to_decimal(annual) or Decimal('0.00')
    try:
        m = int(months or 12)
    except (TypeError, ValueError):
        m = 12
    if net <= 0 or m >= 12 or m <= 0:
        return None
    charged = (net * Decimal(m) / Decimal('12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {
        'months': m,
        'annual': net,
        'charged': charged,
        'fraction': f'{m}/12',
    }
