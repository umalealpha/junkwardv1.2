"""
finance_report/engine.py — the monthly Premium / Claims / Loss Ratio report.

Written from the finance team's own walkthrough (Bokani Makosha, 31-Aug-2026).
Three tabs that build on each other: premium, claims, then claims divided by
premium. Nothing here reads a database or a file — it takes normalised rows and
returns tables, so the arithmetic can be tested without a spreadsheet in sight.

The whole difficulty of this report is that the source reports describe the same
policies in different languages, and the translation is where the mistakes live:

  * The Premium Board has no rows at all for Instant Insurance or Motor
    Comprehensive - those premiums come from the Month-on-Month report instead.
    In the CLAIMS report the same two products DO appear, under the MIS prefix.
    So "which report does this product come from" changes between the premium
    tab and the claims tab, for the same product.
  * The Month-on-Month report has no regulatory mapping column, so each of its
    product names has to be translated by hand before it can be added to the
    Premium Board's own regulatory totals.
  * Two Month-on-Month products fold into ONE regulatory row. Keeping them
    separate silently breaks the check that Table 4 ties back to Table 1.

Every table carries a TOTAL row, and the totals of Tables 1 to 4 must agree.
`reconcile()` states that out loud rather than leaving it to the reader.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
ZERO = Decimal('0')

# ── Policy-number prefixes ───────────────────────────────────────────────────
# The prefix is the fastest way to sort thousands of rows without reading a
# single product name. MIS appears only in the claims report: in the premium
# tab those two products come from the Month-on-Month report instead.
PERSONAL_LINES = 'Personal Lines'
CORPORATE_LINES = 'Corporate Lines'
INSTANT_INSURANCE = 'Instant Insurance'
MOTOR_COMPREHENSIVE = 'Motor Comprehensive'
UNION_LEGAL = 'Union Legal Insurance'
HEALTH_INSURANCE = 'Health Insurance'

PREFIX_TO_LINE = {
    'DOMG': PERSONAL_LINES,
    'COMG': CORPORATE_LINES,
}

MOTOR = 'Motor'
NON_MOTOR = 'Non-Motor'

# ── Month-on-Month product -> regulatory mapping name ────────────────────────
# The Month-on-Month report carries product names only. Hospital Cashback and
# Legal are two products that share ONE regulatory row; adding them together is
# the step people miss, and it is exactly what breaks the Table 4 = Table 1 tie.
HOSPITAL_CASHBACK_AND_LEGAL = 'Hospital Cashback and Legal'
MOM_PRODUCT_TO_REGULATORY = {
    'accidental death insurance': 'Accident',
    'hospital cashback insurance': HOSPITAL_CASHBACK_AND_LEGAL,
    'legal insurance': HOSPITAL_CASHBACK_AND_LEGAL,
    'mobile and electronic device insurance': 'Miscellaneous',
    'motor comprehensive': MOTOR,
    'third party car insurance': MOTOR,
}

# Table 3 pulls Third Party Car Insurance out of Instant Insurance and counts it
# as Motor, even though Table 1 leaves it inside Instant Insurance.
THIRD_PARTY_CAR = 'third party car insurance'


def money(value) -> Decimal:
    """Everything monetary goes through here, so rounding happens once and in
    one direction. VAT rounds HALF UP by CFO standing rule; the same convention
    is used for every figure in this report so a total cannot drift from the
    rows that make it."""
    if value is None or value == '':
        return ZERO
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value).replace(',', '').strip())
        except (ArithmeticError, ValueError):
            return ZERO
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def line_for_policy(policy_no: str) -> str | None:
    """Corporate/Personal from the policy-number prefix. Unknown -> None, which
    the callers surface as an unclassified row rather than quietly dropping."""
    p = (policy_no or '').strip().upper()
    for prefix, line in PREFIX_TO_LINE.items():
        if p.startswith(prefix):
            return line
    return None


def is_motor(regulatory_name: str) -> bool:
    return (regulatory_name or '').strip().lower() == 'motor'


def regulatory_for_mom_product(product: str) -> str:
    """Translate a Month-on-Month product name into its regulatory mapping name.
    An unrecognised product is NOT silently folded into Miscellaneous — a new
    product that nobody mapped must show up as itself so it gets noticed."""
    key = (product or '').strip().lower()
    return MOM_PRODUCT_TO_REGULATORY.get(key, (product or 'Unclassified').strip())


# ── Normalised rows ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PremiumRow:
    """One premium figure, already ex-VAT, already assigned to a month."""
    month: str            # 'YYYY-MM'
    line: str             # Corporate Lines / Personal Lines / Instant Insurance / ...
    regulatory: str       # Motor, Accident, Hospital Cashback and Legal, ...
    amount: Decimal
    product: str = ''     # only the Month-on-Month rows carry one


@dataclass(frozen=True)
class ClaimRow:
    """One claim, assigned to a month, carrying BOTH sides of it: what has been
    PAID and what is still RESERVED. The CFO wants each analysed on its own, so
    they are kept apart here and only added together where an incurred figure is
    asked for. Whatever sign the source gave is kept — flipping it would change
    the sign of every loss ratio downstream."""
    month: str
    line: str
    regulatory: str
    claim_type: str
    reserve: Decimal
    paid: Decimal = ZERO


# The three ways a claim figure is read. "Incurred" — paid plus reserve — is the
# true loss the business has taken; the other two are the halves of it.
RESERVE, PAID, INCURRED = 'reserve', 'paid', 'incurred'


def claim_amount(row: 'ClaimRow', measure: str) -> Decimal:
    if measure == PAID:
        return row.paid
    if measure == INCURRED:
        return money(row.paid + row.reserve)
    return row.reserve


@dataclass
class Table:
    """A finished table: named rows, one column per month, plus YTD.

    YTD is the running sum of every month present, not just the last two — as
    months are added the older ones stay in the total.
    """
    title: str
    months: list = field(default_factory=list)
    rows: 'OrderedDict[str, dict]' = field(default_factory=OrderedDict)

    def add(self, name: str, month: str, amount: Decimal) -> None:
        row = self.rows.setdefault(name, {})
        row[month] = money(row.get(month, ZERO) + amount)

    def ensure(self, name: str) -> None:
        """Keep a named row on the table even when it has no figures, so a line
        that went to zero this month reads as zero instead of vanishing."""
        self.rows.setdefault(name, {})

    def ytd(self, name: str) -> Decimal:
        return money(sum(self.rows.get(name, {}).values(), ZERO))

    def total_for(self, month: str) -> Decimal:
        return money(sum(r.get(month, ZERO) for r in self.rows.values()))

    def total_ytd(self) -> Decimal:
        return money(sum(self.ytd(n) for n in self.rows))

    def as_dict(self) -> dict:
        return {
            'title': self.title,
            'months': list(self.months),
            'rows': [
                {
                    'name': name,
                    'months': {m: str(self.rows[name].get(m, ZERO)) for m in self.months},
                    'ytd': str(self.ytd(name)),
                }
                for name in self.rows
            ],
            'total': {
                'months': {m: str(self.total_for(m)) for m in self.months},
                'ytd': str(self.total_ytd()),
            },
        }


def _months_of(rows) -> list:
    return sorted({r.month for r in rows})


# ── Part 1: Premium Analysis ─────────────────────────────────────────────────

def premium_table_1(rows) -> Table:
    """Table 1 — Premium by Product Line.

    Corporate and Personal come from the Premium Board (by policy prefix);
    Instant Insurance and Motor Comprehensive from the Month-on-Month report;
    Union Legal and Health from their own single-line reports.
    """
    t = Table('Premium by Product Line', _months_of(rows))
    for name in (CORPORATE_LINES, PERSONAL_LINES, INSTANT_INSURANCE,
                 MOTOR_COMPREHENSIVE, UNION_LEGAL, HEALTH_INSURANCE):
        t.ensure(name)
    for r in rows:
        t.add(r.line, r.month, r.amount)
    return t


def premium_table_2(rows) -> Table:
    """Table 2 — Further Analysis: Motor / Non-Motor inside each of Corporate
    and Personal. Instant Insurance and Motor Comprehensive carry over from
    Table 1 unsplit, exactly as the walkthrough says."""
    t = Table('Further Analysis (Motor / Non-Motor within each line)', _months_of(rows))
    for r in rows:
        if r.line in (CORPORATE_LINES, PERSONAL_LINES):
            suffix = MOTOR if is_motor(r.regulatory) else NON_MOTOR
            t.add(f'{r.line} {suffix}', r.month, r.amount)
        else:
            t.add(r.line, r.month, r.amount)
    return t


def premium_table_3(rows) -> Table:
    """Table 3 — Motor vs Non-Motor for the whole company.

    Motor is Corporate Motor + Personal Motor + Motor Comprehensive + Third
    Party Car Insurance. Third Party Car sits inside Instant Insurance in
    Table 1 and has to be pulled out here — that is the only new work in this
    table, and the only place the two tables legitimately disagree on where a
    product lives. Non-Motor is everything else, so the total still ties.
    """
    t = Table('Motor vs Non-Motor Split', _months_of(rows))
    t.ensure(MOTOR)
    t.ensure(NON_MOTOR)
    for r in rows:
        if r.line in (CORPORATE_LINES, PERSONAL_LINES):
            bucket = MOTOR if is_motor(r.regulatory) else NON_MOTOR
        elif r.line == MOTOR_COMPREHENSIVE:
            bucket = MOTOR
        elif (r.product or '').strip().lower() == THIRD_PARTY_CAR:
            bucket = MOTOR
        else:
            bucket = NON_MOTOR
        t.add(bucket, r.month, r.amount)
    return t


def premium_table_4(rows) -> Table:
    """Table 4 — Detailed Breakdown by Regulatory Mapping.

    The Premium Board rows already carry a regulatory class. The Month-on-Month
    rows do not, so they were translated on the way in. Both are simply grouped
    here — which is why the translation has to be right before this point.
    """
    t = Table('Detailed Breakdown by Regulatory Mapping', _months_of(rows))
    for r in rows:
        t.add(r.regulatory or 'Unclassified', r.month, r.amount)
    return t


# ── Part 2: Claims Analysis ──────────────────────────────────────────────────

def claims_table_1(rows, measure=RESERVE) -> Table:
    t = Table('Claims by Product Line', _months_of(rows))
    for name in (CORPORATE_LINES, PERSONAL_LINES, INSTANT_INSURANCE, MOTOR_COMPREHENSIVE):
        t.ensure(name)
    for r in rows:
        t.add(r.line, r.month, claim_amount(r, measure))
    return t


def claims_table_2(rows, measure=RESERVE) -> Table:
    t = Table('Further Analysis (Motor / Non-Motor within each line)', _months_of(rows))
    for r in rows:
        amount = claim_amount(r, measure)
        if r.line in (CORPORATE_LINES, PERSONAL_LINES):
            suffix = MOTOR if is_motor(r.regulatory) else NON_MOTOR
            t.add(f'{r.line} {suffix}', r.month, amount)
        else:
            t.add(r.line, r.month, amount)
    return t


def claims_table_3(rows, measure=RESERVE) -> Table:
    """Simpler than its premium twin: one report, one column. Anything the
    regulatory mapping calls MOTOR is Motor; everything else is not."""
    t = Table('Motor vs Non-Motor Split', _months_of(rows))
    t.ensure(MOTOR)
    t.ensure(NON_MOTOR)
    for r in rows:
        t.add(MOTOR if is_motor(r.regulatory) else NON_MOTOR, r.month, claim_amount(r, measure))
    return t


def claims_table_4(rows, measure=RESERVE) -> Table:
    """Table 4 — by regulatory mapping, with one row built by hand.

    The claims report's regulatory mapping has no Hospital Cashback and Legal
    row at all. Those claims are identifiable only by Claim Type, so they are
    pulled out and combined under the SAME row name the premium tab uses —
    otherwise the loss-ratio tab has a premium row with no claims row to divide.
    """
    t = Table('Detailed Breakdown by Regulatory Mapping', _months_of(rows))
    for r in rows:
        amount = claim_amount(r, measure)
        ct = (r.claim_type or '').strip().lower()
        if ct in ('hospital cashback', 'legal insurance'):
            t.add(HOSPITAL_CASHBACK_AND_LEGAL, r.month, amount)
        else:
            t.add(r.regulatory or 'Unclassified', r.month, amount)
    return t


# ── Part 3: Loss Ratio ───────────────────────────────────────────────────────

def _pct(numer: Decimal, denom: Decimal):
    return (numer / denom * 100).quantize(CENT, rounding=ROUND_HALF_UP) if denom else None


def loss_ratio_table(title: str, premium: Table, reserve: Table, paid: Table) -> dict:
    """Claims / Premium, row by row, off the YTD totals of the tabs above — and
    the CFO wants paid and reserved analysed separately, so every row carries
    BOTH: what has been paid, what is still reserved, and the incurred total of
    the two. The loss ratio is shown on paid and on incurred; incurred is the
    real one and is what the High flag reads.

    Reserves are held negative in some cuts of the claims report, so the ratios
    come out negative too — the report's own convention, left alone not tidied.

    "Status" is High when a row runs materially hotter than the book as a whole,
    judged against THIS report's own incurred loss ratio rather than a fixed
    cutoff that would silently go stale.
    """
    names = list(premium.rows.keys())
    for src in (reserve, paid):
        names += [n for n in src.rows if n not in names]

    total_premium = premium.total_ytd()
    total_reserve = reserve.total_ytd()
    total_paid = paid.total_ytd()
    total_incurred = money(total_reserve + total_paid)
    book_lr = _pct(total_incurred, total_premium)

    out = []
    for name in names:
        p = premium.ytd(name)
        res = reserve.ytd(name)
        pd = paid.ytd(name)
        inc = money(res + pd)
        lr_incurred = _pct(inc, p)
        lr_paid = _pct(pd, p)
        share = _pct(p, total_premium)

        if lr_incurred is None or book_lr is None:
            status = 'No premium' if not p else 'Good'
        else:
            status = 'High' if abs(lr_incurred) > abs(book_lr) * Decimal('1.5') else 'Good'

        out.append({
            'name': name,
            'premium_ytd': str(p),
            'paid_ytd': str(pd),
            'reserve_ytd': str(res),
            'claims_ytd': str(inc),          # incurred; kept under the old key so nothing breaks
            'incurred_ytd': str(inc),
            'loss_ratio_pct': str(lr_incurred) if lr_incurred is not None else None,
            'loss_ratio_paid_pct': str(lr_paid) if lr_paid is not None else None,
            'pct_of_premium': str(share) if share is not None else None,
            'status': status,
        })

    return {
        'title': title,
        'rows': out,
        'total': {
            'premium_ytd': str(total_premium),
            'paid_ytd': str(total_paid),
            'reserve_ytd': str(total_reserve),
            'claims_ytd': str(total_incurred),
            'incurred_ytd': str(total_incurred),
            'loss_ratio_pct': str(book_lr) if book_lr is not None else None,
            'loss_ratio_paid_pct': str(_pct(total_paid, total_premium)) if total_premium else None,
            'pct_of_premium': '100.00' if total_premium else None,
        },
    }


# ── The tie-back check ───────────────────────────────────────────────────────

def reconcile(tables) -> dict:
    """Tables 1 to 4 describe the same money four ways, so their totals must
    agree. When they do not, the cause is almost always the Month-on-Month
    translation in Table 4 — a product mapped twice, or one not mapped at all.

    Returning this rather than asserting it means the report still renders when
    it breaks, with the break visible, instead of failing to open.
    """
    totals = [t.total_ytd() for t in tables]
    ok = all(t == totals[0] for t in totals)
    return {
        'balanced': ok,
        'totals': [str(t) for t in totals],
        'difference': str(money(max(totals) - min(totals))) if totals else '0.00',
        'note': ('' if ok else
                 'Tables do not tie. Check the Month-on-Month regulatory translation '
                 'in Table 4 — a product mapped twice or not at all is the usual cause.'),
    }


def build_report(premium_rows, claims_rows, months=None) -> dict:
    """The whole report: three tabs, plus the tie-back check for each side.

    `months` is the reporting period as a list of 'YYYY-MM', and it matters more
    than it looks. The Claims As On Date export is inception-to-date: it carries
    every claim ever reported, while the Premium Board is a period cut. Divide
    one by the other unfiltered and the loss ratio comes out over 100% on a book
    that is nothing of the sort — the first real run of this engine did exactly
    that (127.2m premium against 133.0m of all-time reserves).

    Left as None the period is taken from the PREMIUM rows, which is the safer
    default of the two: premium is the denominator, and a claim in a month with
    no premium has nothing to divide into.
    """
    if months is None:
        months = _months_of(premium_rows)
    months = sorted(set(months))
    if months:
        keep = set(months)
        excluded_claims = [r for r in claims_rows if r.month not in keep]
        excluded_premium = [r for r in premium_rows if r.month not in keep]
        claims_rows = [r for r in claims_rows if r.month in keep]
        premium_rows = [r for r in premium_rows if r.month in keep]
    else:
        excluded_claims, excluded_premium = [], []

    p1, p2 = premium_table_1(premium_rows), premium_table_2(premium_rows)
    p3, p4 = premium_table_3(premium_rows), premium_table_4(premium_rows)

    # Claims built twice — once on reserves, once on payments — so each can be
    # analysed on its own, as the CFO asked. The loss-ratio tab then divides the
    # premium by both, and by their sum.
    cr = [claims_table_1(claims_rows, RESERVE), claims_table_2(claims_rows, RESERVE),
          claims_table_3(claims_rows, RESERVE), claims_table_4(claims_rows, RESERVE)]
    cp = [claims_table_1(claims_rows, PAID), claims_table_2(claims_rows, PAID),
          claims_table_3(claims_rows, PAID), claims_table_4(claims_rows, PAID)]

    return {
        'premium': {
            'tables': [t.as_dict() for t in (p1, p2, p3, p4)],
            'reconciliation': reconcile([p1, p2, p3, p4]),
        },
        'claims': {
            'reserve': {
                'tables': [t.as_dict() for t in cr],
                'reconciliation': reconcile(cr),
            },
            'paid': {
                'tables': [t.as_dict() for t in cp],
                'reconciliation': reconcile(cp),
            },
        },
        'loss_ratio': [
            loss_ratio_table('Loss Ratio by Product Line', p1, cr[0], cp[0]),
            loss_ratio_table('Motor vs Non-Motor Loss Ratio', p3, cr[2], cp[2]),
            loss_ratio_table('Detailed Loss Ratio by Regulatory Mapping', p4, cr[3], cp[3]),
        ],
        'months': months,
        'period': {
            'months': months,
            'from': months[0] if months else None,
            'to': months[-1] if months else None,
            'claims_rows_outside_period': len(excluded_claims),
            'premium_rows_outside_period': len(excluded_premium),
            'note': ('Claims As On Date is an inception-to-date export, so it carries '
                     'claims reported before this period. Those are excluded here — '
                     'dividing all-time reserves by one period of premium overstates '
                     'the loss ratio.') if excluded_claims else '',
        },
    }
