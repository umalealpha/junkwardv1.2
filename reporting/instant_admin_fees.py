"""reporting/instant_admin_fees.py — the monthly Instant Insurance admin fees.

B5 of the Omni Build Spec (13 September 2026), requested by Bokani Makosha.

WHAT IT IS. Once a month, on the 26th, the four retail partners who sell Instant
Insurance over their own counters are due an admin fee on the premium they wrote.
The period runs from the 27th of the previous month to the 26th of this one. Today
that report is built by hand off a Graphite export.

WHY IT LIVES IN ``reporting`` AND NOT IN A NEW ``instant`` APP.
The spec offers ``instant/`` or ``realpay/instant_admin_fees.py`` and asks us to
decide by where the instant policy data already lives. It lives in exactly one
place: **Graphite**. Omni holds no instant policy model, no store table and no
premium of its own — ``integrations/instant_insurance_views.py`` already reads this
same book live through ``integrations.graphite_ro``, and this module reads it the
same way, off the same columns, with the same product-name list. So:

  * a new ``instant`` app would be an app with no models and no migrations, added
    only to hold two files;
  * ``realpay`` is the debit-order collector. It owns collections, not commission,
    and nothing about an admin fee is a RealPay concern;
  * ``reporting`` is where Omni's monthly, Graphite-sourced finance reports already
    sit (``graphite_age_analysis``, ``graphite_payments``, ``premium_lapse``), and
    it is also the home of ``ReportRecipient``, the list Finance keep themselves.

NO GL POSTING. The spec is explicit and so is the house rule: the commission goes
to Finance for review and posting, which is a human step. This module SELECTs and
builds a spreadsheet. It posts no journal, raises no payment and moves no money.
The GL account codes below are carried for Finance's convenience when they come to
post it by hand — they are printed, never used.

🔴 THE ARITHMETIC, AND WHY THE SPEC'S OWN WORKED EXAMPLE IS NOT FOLLOWED.
The spec prints this for Choppies:

    3,127.00 -> 1,250.80 -> (153.61) -> 1,097.19 -> (109.72) -> **1,141.08**

The first four steps are right: 40% of 3,127.00 is 1,250.80; divide by 1.14 and
1,097.19 is left, the 153.61 being the VAT; 10% of 1,097.19 is 109.72. But
1,097.19 - 109.72 is **987.47**. The printed 1,141.08 is 1,250.80 - 109.72 — the
VAT put back before the tax came off, which contradicts the document's own steps.

**CFO decision, 13 September 2026: the answer is 987.47.** The withholding tax is
charged on the fee, not on the VAT, because the VAT is not our income. The figure
1,141.08 is wrong and is not reproduced anywhere in this build. See
``test_instant_admin_fees.ChoppiesWorkedExampleTests``.

THE ONE THING TO CONFIRM ON THE LIVE REPLICA BEFORE GO-LIVE. A store is matched on
the name of the agency recorded ON THE POLICY (``policies.agency_id`` -> ``agencies.name``),
which is the attribution rule ``aware.modes.broker_analysis`` already uses and the
one ``aware/tests_broker_join.py`` exists to defend. The patterns in ``MERCHANTS``
were written from the chart-of-accounts spellings, not read off Graphite. Run the
command with ``--dry-run`` once: it prints the agency names each pattern actually
matched, so a wrong spelling is caught in one run rather than by a partner asking
where their fee went. A pattern that matches nothing does NOT go quiet — the
merchant is still named in the report, saying it had none.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from django.conf import settings
from django.utils import timezone

from integrations import graphite_ro
from integrations.instant_insurance_views import INSTANT_PRODUCTS

log = logging.getLogger(__name__)

#: The day of the month the period closes on, and the day the report is generated.
PERIOD_END_DAY = 26

#: Commission on premium, before anything is stripped or deducted.
COMMISSION_RATE = Decimal('0.40')

#: Withholding tax on the fee. Botswana OWHT on commission.
OWHT_RATE = Decimal('0.10')

#: Two decimal places. Money.
CENT = Decimal('0.01')

#: Upper bound on one run, so a widened window can never stream the whole table.
MAX_ROWS = 5000


class Merchant(NamedTuple):
    """One of the four retail partners.

    ``patterns`` are SQL LIKE patterns matched case-insensitively against the
    agency name on the policy. ``gl_account`` is printed for Finance to post
    against by hand; nothing here posts it.
    """
    name: str
    patterns: Tuple[str, ...]
    gl_account: str


#: THE FOUR, AND ONLY THESE FOUR (spec B5). The order is the report's order.
#: GL codes from ops/seeds/seed_coa_v2.py — carried for Finance, never posted here.
MERCHANTS: Tuple[Merchant, ...] = (
    Merchant('Choppies',    ('%choppies%',),                  '107011'),
    Merchant('Sefalana',    ('%sefalana%',),                  '107010'),
    Merchant('Trans',       ('%trans cash%', '%trans & carry%',
                             '%trans and carry%', '%trans cash & carry%'), '92'),
    Merchant('Yash Cell',   ('%yash%',),                      '91'),
)


class GraphiteUnavailable(RuntimeError):
    """We could not look. Never to be reported as "there were none"."""


# ---------------------------------------------------------------------------
# The period — 27th to 26th, in Botswana time
# ---------------------------------------------------------------------------

def period_window(year: int, month: int) -> Tuple[datetime.date, datetime.date]:
    """(start, end) inclusive: the 27th of the previous month to the 26th of this.

    December to January is the case that bites: the start is the 27th of DECEMBER
    OF THE PREVIOUS YEAR, not of the same year. ``month - 1`` on its own gives
    month 0 and raises; the year has to move with it.
    """
    month = int(month)
    year = int(year)
    if not 1 <= month <= 12:
        raise ValueError(f'month must be 1..12, got {month}')
    end = datetime.date(year, month, PERIOD_END_DAY)
    if month == 1:
        start = datetime.date(year - 1, 12, PERIOD_END_DAY + 1)
    else:
        start = datetime.date(year, month - 1, PERIOD_END_DAY + 1)
    return start, end


def current_period(today: Optional[datetime.date] = None) -> Tuple[int, int]:
    """The period that has most recently CLOSED, in Botswana time.

    ``timezone.localdate()``, never ``date.today()`` — the server runs on UTC and
    Gaborone is two hours ahead, so an evening run would otherwise take yesterday's
    date and, on the 26th itself, report the wrong month.

    On or after the 26th the current month's period has closed. Before it, the last
    closed period is the previous month's — so a manual run on the 3rd reports the
    month just gone rather than a window running into the future.
    """
    d = today or timezone.localdate()
    if d.day >= PERIOD_END_DAY:
        return d.year, d.month
    if d.month == 1:
        return d.year - 1, 12
    return d.year, d.month - 1


def period_label(year: int, month: int) -> str:
    """'27 Dec 2026 to 26 Jan 2027' — what the report calls the period."""
    start, end = period_window(year, month)
    return f'{start:%d %b %Y} to {end:%d %b %Y}'


# ---------------------------------------------------------------------------
# The calculation
# ---------------------------------------------------------------------------

def _money(v) -> Decimal:
    """An amount as money, HALF UP.

    Never ``round()``: Python rounds half to EVEN, so 0.125 becomes 0.12. Rounding
    is a tax decision here (this feeds a VAT figure and a withholding figure), and
    a language default is not allowed to make it.
    """
    if v is None or v == '':
        return Decimal('0.00')
    try:
        return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def vat_divisor() -> Decimal:
    """1 + the VAT rate, read from settings.

    ``settings.RC_VAT_RATE`` is Omni's ONE VAT rate (alpha_finance/settings.py).
    A second constant here would be a second place to change when BURS move the
    rate, and the two would disagree within a year. The spec asks for the rate to
    be a parameter and never the literal 1.14; this is that parameter.
    """
    return Decimal('1') + Decimal(str(settings.RC_VAT_RATE))


def fees(premium) -> Dict[str, Decimal]:
    """The three steps, in the order the spec gives them.

    Step 1  commission = 40% of premium.
    Step 2  divide by (1 + VAT rate) to strip the VAT out of it.
    Step 3  deduct 10% OWHT **from the VAT-exclusive fee**.

    Worked example (CFO 13-Sep-2026), Choppies, premium 3,127.00:
        commission  1,250.80
        vat           153.61
        net_of_vat  1,097.19
        owht          109.72
        payable       987.47     <- 1,097.19 - 109.72

    The spec document prints 1,141.08 as the answer. It is wrong: that is
    1,250.80 - 109.72, i.e. the tax taken off before the VAT was stripped, which
    contradicts the document's own three steps. The CFO ruled on 13 September 2026
    that 987.47 is correct — the 10% is charged on the fee, not on the VAT,
    because the VAT is not our income. 1,141.08 appears nowhere in this build.

    Every intermediate is rounded to the thebe as it is produced, so the report's
    columns add up to its own totals. A reader who checks one row with a
    calculator gets the same answer we printed.
    """
    gross = Decimal(str(premium or 0))
    commission = (gross * COMMISSION_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    net_of_vat = (commission / vat_divisor()).quantize(CENT, rounding=ROUND_HALF_UP)
    vat = commission - net_of_vat
    owht = (net_of_vat * OWHT_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
    payable = net_of_vat - owht
    return {
        'premium': _money(gross),
        'commission': commission,
        'vat': vat,
        'net_of_vat': net_of_vat,
        'owht': owht,
        'payable': payable,
    }


# ---------------------------------------------------------------------------
# Reading Graphite
# ---------------------------------------------------------------------------

def _ph(values: Sequence[Any]) -> str:
    return ', '.join(['%s'] * len(values))


def _select(plan_expr: str, plan_join: str, n_products: int, n_patterns: int) -> str:
    """The pivot statement. ``plan_expr``/``plan_join`` vary; nothing else does."""
    return (
        'SELECT a.name AS store, p.name AS product, '
        f'{plan_expr} AS plan, '
        'COUNT(*) AS policies, SUM(pol.premium) AS premium '
        'FROM policies pol '
        'JOIN products p ON p.id = pol.product_id '
        'JOIN agencies a ON a.id = pol.agency_id '
        f'{plan_join} '
        f'WHERE p.name IN ({", ".join(["%s"] * n_products)}) '
        '  AND pol.status = 1 '
        '  AND pol.policyActivatedDate IS NOT NULL '
        '  AND pol.policyActivatedDate >= %s '
        '  AND pol.policyActivatedDate <= %s '
        f'  AND ({" OR ".join(["LOWER(a.name) LIKE %s"] * n_patterns)}) '
        f'GROUP BY a.name, p.name, {plan_expr} '
        'ORDER BY a.name, p.name, plan'
    )


def raw_rows(start: datetime.date, end: datetime.date) -> Tuple[List[Dict[str, Any]], bool]:
    """(rows, plan_available) straight off the replica, unfiltered by merchant.

    ACTIVE policies only, inception inside the window, the five Instant products
    only — the same three rules ``integrations.instant_insurance_views`` applies,
    against the same columns, so the two screens can never disagree about what an
    Instant policy is.

    The product plan is the one column this could not be verified against the live
    schema from the build machine. It is attempted as ``product_plans.name`` and,
    if that join is not there, the statement is re-run without it and
    ``plan_available`` comes back False — the report then says so on its face
    rather than failing outright and sending nothing. A missing plan column is a
    less serious problem than no report at all, but it is never hidden.

    Raises GraphiteUnavailable when the replica cannot be read at all.
    """
    if not graphite_ro.is_configured():
        raise GraphiteUnavailable('Graphite read-only bridge is not configured.')

    patterns = [p.lower() for m in MERCHANTS for p in m.patterns]
    params = [
        *INSTANT_PRODUCTS,
        start.isoformat(),
        end.isoformat(),
        *patterns,
    ]

    attempts = (
        ('pp.name', 'LEFT JOIN product_plans pp ON pp.id = pol.product_plan_id', True),
        ("''", '', False),
    )
    last: Optional[Exception] = None
    for plan_expr, plan_join, available in attempts:
        sql = _select(plan_expr, plan_join, len(INSTANT_PRODUCTS), len(patterns))
        try:
            return list(graphite_ro.query(sql, params, limit=MAX_ROWS) or []), available
        except graphite_ro.GraphiteReadOnlyError:
            raise
        except Exception as exc:                       # noqa: BLE001 — see docstring
            log.warning('instant admin fees: plan variant failed (%s)',
                        exc.__class__.__name__)
            last = exc
    raise GraphiteUnavailable(str(last) if last else 'Graphite is unreachable.')


def _merchant_for(store_name: str) -> Optional[Merchant]:
    """Which of the four a Graphite agency name belongs to, or None.

    Matched here rather than trusted from the SQL so the pattern list is one place
    and the same rule decides both what is fetched and what it is filed under.
    """
    low = (store_name or '').strip().lower()
    for m in MERCHANTS:
        for pat in m.patterns:
            if _like(low, pat.lower()):
                return m
    return None


def _like(value: str, pattern: str) -> bool:
    """A LIKE with only leading/trailing % — which is all MERCHANTS uses."""
    core = pattern.strip('%')
    if pattern.startswith('%') and pattern.endswith('%'):
        return core in value
    if pattern.startswith('%'):
        return value.endswith(core)
    if pattern.endswith('%'):
        return value.startswith(core)
    return value == core


def collect(year: int, month: int) -> Dict[str, Any]:
    """The whole report for one period, one block per merchant.

    EVERY ONE OF THE FOUR MERCHANTS IS IN THE RESULT, ALWAYS — including the ones
    with nothing. A merchant missing from a report reads as an oversight; a
    merchant named and reported as nil reads as "no business this month", which is
    what it is. That is the spec's own rule and it is why ``blocks`` is built from
    MERCHANTS and not from the rows that came back.
    """
    start, end = period_window(year, month)
    rows, plan_available = raw_rows(start, end)

    buckets: Dict[str, List[Dict[str, Any]]] = {m.name: [] for m in MERCHANTS}
    matched_names: Dict[str, set] = {m.name: set() for m in MERCHANTS}
    unmatched: Dict[str, Decimal] = {}

    for r in rows:
        store = (r.get('store') or '').strip()
        merchant = _merchant_for(store)
        premium = _money(r.get('premium'))
        if merchant is None:
            # Cannot happen with the SQL filter above, but a pattern edited in one
            # place and not the other would make it happen silently. Reported.
            unmatched[store] = unmatched.get(store, Decimal('0.00')) + premium
            continue
        buckets[merchant.name].append({
            'product': (r.get('product') or '').strip() or 'Unknown product',
            'plan': (r.get('plan') or '').strip() or '—',
            'policies': int(r.get('policies') or 0),
            'premium': premium,
        })
        matched_names[merchant.name].add(store)

    blocks: List[Dict[str, Any]] = []
    # BUILT FROM MERCHANTS, NEVER FROM THE ROWS THAT CAME BACK. A merchant with
    # nothing still gets a block, marked nil and carrying its own sentence.
    for m in MERCHANTS:
        lines = sorted(buckets[m.name], key=lambda x: (-x['premium'], x['product'], x['plan']))
        premium = sum((l['premium'] for l in lines), Decimal('0.00'))
        for line in lines:
            line.update(fees(line['premium']))
        blocks.append({
            'merchant': m.name,
            'gl_account': m.gl_account,
            'rows': lines,
            'policies': sum(l['policies'] for l in lines),
            'source_names': sorted(matched_names[m.name]),
            # The totals are computed on the merchant's OWN premium, not summed
            # off the rounded rows: rounding each product line and adding them up
            # can land a thebe or two away from the figure the partner will
            # calculate from their own premium total, and that is the argument
            # nobody wants to have on a fee note.
            **fees(premium),
            'nil': not lines,
            'nil_message': nil_message(m.name, year, month),
        })

    # THE GRAND TOTAL IS THE SUM OF WHAT EACH STORE IS PAID, not a fresh
    # calculation on the combined premium. Those two differ by a thebe or two,
    # and the one that has to be right is the one that equals the four amounts
    # actually going out — a headline that does not equal its own rows is the
    # fastest way to lose the reader.
    totals = {k: sum((b[k] for b in blocks), Decimal('0.00'))
              for k in ('premium', 'commission', 'vat', 'net_of_vat',
                        'owht', 'payable')}
    return {
        'year': year,
        'month': month,
        'start': start,
        'end': end,
        'label': period_label(year, month),
        'blocks': blocks,
        'plan_available': plan_available,
        'unmatched': unmatched,
        'vat_rate': Decimal(str(settings.RC_VAT_RATE)),
        'policies': sum(b['policies'] for b in blocks),
        **totals,
    }


def nil_message(merchant: str, year: int, month: int) -> str:
    """The sentence the spec dictates, word for word.

    "If a merchant has zero transactions, the report must SAY: 'No instant
    insurance admin fees recorded for [Store Name] in [Period].' Never omit the
    merchant silently."
    """
    return (f'No instant insurance admin fees recorded for {merchant} in '
            f'{period_label(year, month)}.')
