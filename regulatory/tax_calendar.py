"""
regulatory/tax_calendar.py — Botswana statutory tax deadlines, the AUTHORITATIVE
computation. (Feature request by Oprah Mogomotsi, 2026-09-11; CFO approved.)

WHY THIS FILE EXISTS
--------------------
Omni already had a Tax Calendar, but it lived entirely in the browser
(`frontend/src/app/(dashboard)/tax-calendar/deadlines.ts`) and it was built on
the PRE-2026 deadlines. The Income Tax Act 2026, VAT Act 2026 and Tax
Administration Act 2026 took effect 1 July 2026 and moved almost every date:

    obligation      old page said                new Acts say
    ------------    -------------------------    ------------------------------
    VAT             last day of the month        25th of the following month*
    PAYE            15th                         14th after month-end
    OWHT            end of 2nd month after       14th after month-end
    SAT (company)   two provisionals, Jun/Dec    four quarterly instalments
    annual PAYE     31 October                   28 days after tax year-end

A calendar that is confidently wrong is worse than no calendar, so the dates now
live HERE, in Python, computed once, and the page reads them. Nothing computes a
statutory date in TypeScript any more.

Dates cross-checked by the reporter against Andersen Tax, KPMG and Payspace —
all three agreed — and confirmed by the CFO on 2026-09-11, with one correction:

  * VAT is due on the 25th, not the 28th the advisers quote. The CFO is the
    authority on what Alpha Direct is actually held to. See VAT_DUE_DAY.

TIMEZONE
--------
Every date in this module is a plain `datetime.date` in Gaborone terms. Callers
must obtain "today" from `regulatory.tax_workflow.today_gabs()`, which wraps
`django.utils.timezone.localdate()`,
never `date.today()`, which returns the server's UTC day and rolls over two
hours early — the midnight-clock bug this codebase has already fixed twice.

THE TARGET DATE
---------------
Each obligation carries a statutory `due_date` and an internal `target_date`,
which is `due_date - REMINDER_LEAD_DAYS` (10 days). The target is what the
reminder fires on and what lateness is measured against. Missing the TARGET is
an internal service failure; missing the DUE DATE is a live BURS breach. They
are deliberately different things and are reported differently.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

# Ten days before the due date the reminder starts, so the filing is prepared and
# PAID ten days early rather than on the deadline. CFO-approved 2026-09-11.
REMINDER_LEAD_DAYS = 10

# The day of the month VAT is due. CFO correction 2026-09-11: Alpha Direct pays
# on the 25th. Andersen/KPMG/Payspace all quote the 28th from the VAT Act 2026,
# and the reporter's brief used that — but the CFO is the authority on what this
# company is actually held to, and working back from the 28th would start the
# preparer three days late every single cycle. Overridable per-company through
# TaxComplianceSettings.vat_due_day.
VAT_DUE_DAY = 25


class TaxType:
    """The five statutory obligations this module schedules."""
    VAT           = 'vat'
    PAYE          = 'paye'
    OWHT          = 'owht'
    PAYE_ANNUAL   = 'paye_annual'
    SAT_QUARTERLY = 'sat_quarterly'
    SAT_ANNUAL    = 'sat_annual'

    CHOICES = [
        (VAT,           'VAT return and payment'),
        (PAYE,          'PAYE monthly remittance'),
        (OWHT,          'OWHT monthly remittance'),
        (PAYE_ANNUAL,   'Annual PAYE/OWHT return'),
        (SAT_QUARTERLY, 'SAT quarterly instalment'),
        (SAT_ANNUAL,    'SAT annual return and final balance'),
    ]

    LABELS = dict(CHOICES)


class VatCycle:
    """
    How often BURS requires the VAT return.

    Alpha Direct is CATEGORY_B — two-monthly periods ending in the EVEN months
    (Feb, Apr, Jun, Aug, Oct, Dec). Confirmed by the CFO on 2026-09-11, following
    the reporter's cross-check.

    The other two are implemented because the registration category is a BURS
    decision that can change with turnover, and when it changes we must be able
    to flip a setting rather than ship code. Category A is the odd-month
    two-monthly cycle; MONTHLY applies above the BURS turnover threshold.
    """
    CATEGORY_B = 'category_b'
    CATEGORY_A = 'category_a'
    MONTHLY    = 'monthly'

    CHOICES = [
        (CATEGORY_B, 'Two-monthly, Category B (periods end Feb/Apr/Jun/Aug/Oct/Dec)'),
        (CATEGORY_A, 'Two-monthly, Category A (periods end Jan/Mar/May/Jul/Sep/Nov)'),
        (MONTHLY,    'Monthly'),
    ]


@dataclass(frozen=True)
class Obligation:
    """One statutory filing, fully dated. Pure data — no database, no clock."""
    tax_type:     str
    period_label: str          # human: "Jan–Feb 2027", "March 2027", "FY2026/27"
    period_start: date
    period_end:   date
    due_date:     date         # the statutory deadline. Missing it is a breach.
    key:          str          # stable identity, so re-running never duplicates

    @property
    def target_date(self) -> date:
        """Internal deadline: 10 days before the statutory one."""
        return self.due_date - timedelta(days=REMINDER_LEAD_DAYS)

    @property
    def label(self) -> str:
        return f'{TaxType.LABELS[self.tax_type]} — {self.period_label}'


# ── date helpers ─────────────────────────────────────────────────────────────

def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _add_months(d: date, months: int) -> date:
    """Shift by whole months, clamping the day to the shorter month (31 Jan + 1
    month = 28/29 Feb). Used for period arithmetic, never for a due date."""
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _day_in_month(year: int, month: int, day: int) -> date:
    """`day` of that month, clamped if the month is short. The 28th and the 14th
    both exist in every month, so the clamp never actually fires for the
    statutory dates — it is here so a future rule change to e.g. the 30th cannot
    raise ValueError in February."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


MONTH_NAMES = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
]


def _short(month: int) -> str:
    return MONTH_NAMES[month][:3]


def vat_due_date(period_end: date, due_day: int = VAT_DUE_DAY) -> date:
    """The VAT return/payment due date for a period ending on `period_end`:
    `due_day` of the FOLLOWING month.

    `vat_obligations()` below walks a window and emits whole Obligation objects;
    this is the same rule applied to one known period end, which is what the VAT
    reconciliation (regulatory/vat_recon.py) needs. Both read VAT_DUE_DAY, so the
    CFO's 25th lives in exactly one place.
    """
    if period_end.month == 12:
        due_year, due_month = period_end.year + 1, 1
    else:
        due_year, due_month = period_end.year, period_end.month + 1
    return _day_in_month(due_year, due_month, due_day)


def vat_prepare_by_date(period_end: date, due_day: int = VAT_DUE_DAY) -> date:
    """When the return must be PREPARED: REMINDER_LEAD_DAYS before it is due.

    With the CFO's 25th and the standard ten-day lead this lands on the 15th of
    the month following the period — the date the build spec asks for. It is
    DERIVED, not typed, so if either the due day or the lead ever moves, the
    preparation date moves with it instead of quietly going stale.
    """
    return vat_due_date(period_end, due_day) - timedelta(days=REMINDER_LEAD_DAYS)


# ── the individual schedules ─────────────────────────────────────────────────

def vat_obligations(
    start: date,
    end: date,
    cycle: str = VatCycle.CATEGORY_B,
    due_day: int = VAT_DUE_DAY,
) -> list[Obligation]:
    """
    VAT return AND payment: `due_day` of the month following the tax period.

    The day is 25, NOT the 28 the tax advisers quote. The CFO corrected this on
    2026-09-11: Alpha Direct pays VAT on the 25th. That is the date this company
    is actually held to, so it is the date the reminder must work back from — the
    28th would have had the preparer starting three days late every cycle.

    Category B periods end in even months, Category A in odd months, and the
    MONTHLY cycle files one period per month.
    """
    out: list[Obligation] = []

    if cycle == VatCycle.MONTHLY:
        period_len, end_months = 1, list(range(1, 13))
    elif cycle == VatCycle.CATEGORY_A:
        period_len, end_months = 2, [1, 3, 5, 7, 9, 11]
    else:
        period_len, end_months = 2, [2, 4, 6, 8, 10, 12]

    # Walk a year either side of the window so a period whose DUE date lands
    # inside the window is generated even when its period itself does not.
    for year in range(start.year - 1, end.year + 2):
        for end_month in end_months:
            period_end   = _last_day(year, end_month)
            period_start = _add_months(date(year, end_month, 1), -(period_len - 1))

            # Due the 28th of the month AFTER the period ends.
            due_year, due_month = (year + 1, 1) if end_month == 12 else (year, end_month + 1)
            due = _day_in_month(due_year, due_month, due_day)
            if not (start <= due <= end):
                continue

            if period_len == 1:
                label = f'{MONTH_NAMES[end_month]} {year}'
            else:
                label = f'{_short(period_start.month)}–{_short(end_month)} {year}'

            out.append(Obligation(
                tax_type     = TaxType.VAT,
                period_label = label,
                period_start = period_start,
                period_end   = period_end,
                due_date     = due,
                key          = f'vat:{period_end.isoformat()}',
            ))
    return out


def _monthly_remittance(start: date, end: date, tax_type: str) -> list[Obligation]:
    """PAYE and OWHT share one rule: due the 14th day after month-end.

    Built as a recurring rule over the window, not twelve hard-coded dates —
    the reporter asked for exactly this, because hard-coded dates rot.
    """
    out: list[Obligation] = []
    for year in range(start.year - 1, end.year + 2):
        for month in range(1, 13):
            period_start = date(year, month, 1)
            period_end   = _last_day(year, month)
            due_year, due_month = (year + 1, 1) if month == 12 else (year, month + 1)
            due = _day_in_month(due_year, due_month, 14)
            if not (start <= due <= end):
                continue
            out.append(Obligation(
                tax_type     = tax_type,
                period_label = f'{MONTH_NAMES[month]} {year}',
                period_start = period_start,
                period_end   = period_end,
                due_date     = due,
                key          = f'{tax_type}:{period_end.isoformat()}',
            ))
    return out


def paye_obligations(start: date, end: date) -> list[Obligation]:
    return _monthly_remittance(start, end, TaxType.PAYE)


def owht_obligations(start: date, end: date) -> list[Obligation]:
    return _monthly_remittance(start, end, TaxType.OWHT)


def paye_annual_obligations(start: date, end: date, fy_end_month: int = 6) -> list[Obligation]:
    """
    Annual PAYE/OWHT return: 28 days after the tax year-end.

    Alpha Direct's financial year ends 30 June, so the return falls on 28 July.
    `fy_end_month` is a parameter because the rule, not the date, is the thing
    being encoded.
    """
    out: list[Obligation] = []
    for year in range(start.year - 1, end.year + 2):
        year_end = _last_day(year, fy_end_month)
        due = year_end + timedelta(days=28)
        if not (start <= due <= end):
            continue
        out.append(Obligation(
            tax_type     = TaxType.PAYE_ANNUAL,
            period_label = f'FY{year - 1}/{str(year)[2:]}',
            period_start = _add_months(year_end, -11).replace(day=1),
            period_end   = year_end,
            due_date     = due,
            key          = f'paye_annual:{year_end.isoformat()}',
        ))
    return out


def sat_quarterly_obligations(start: date, end: date, fy_end_month: int = 6) -> list[Obligation]:
    """
    SAT (Self-Assessment Tax) quarterly instalments: due on each quarter-end date
    within the financial year.

    For a July–June year the quarters end 30 Sep, 31 Dec, 31 Mar and 30 Jun, and
    the instalment is due ON that date — not some days after it. That is what
    makes SAT the easiest one to miss: there is no grace period at all.
    """
    out: list[Obligation] = []
    fy_start_month = (fy_end_month % 12) + 1

    for year in range(start.year - 2, end.year + 2):
        fy_start = date(year, fy_start_month, 1)
        for q in range(1, 5):
            q_start    = _add_months(fy_start, 3 * (q - 1))
            q_last_mth = _add_months(q_start, 2)
            q_end      = _last_day(q_last_mth.year, q_last_mth.month)
            due = q_end  # the instalment is due on the quarter-end date itself
            if not (start <= due <= end):
                continue
            fy_label = f'FY{fy_start.year}/{str(fy_start.year + 1)[2:]}'
            out.append(Obligation(
                tax_type     = TaxType.SAT_QUARTERLY,
                period_label = f'Q{q} {fy_label} ({_short(q_start.month)}–{_short(q_end.month)})',
                period_start = q_start,
                period_end   = q_end,
                due_date     = due,
                key          = f'sat_q:{q_end.isoformat()}',
            ))
    return out


def sat_annual_obligations(start: date, end: date, fy_end_month: int = 6) -> list[Obligation]:
    """
    SAT annual return and final balancing payment: 4 months after the financial
    year-end. A 30 June year-end therefore falls due 31 October.
    """
    out: list[Obligation] = []
    for year in range(start.year - 1, end.year + 2):
        year_end = _last_day(year, fy_end_month)
        due_month_anchor = _add_months(year_end.replace(day=1), 4)
        due = _last_day(due_month_anchor.year, due_month_anchor.month)
        if not (start <= due <= end):
            continue
        out.append(Obligation(
            tax_type     = TaxType.SAT_ANNUAL,
            period_label = f'FY{year - 1}/{str(year)[2:]}',
            period_start = _add_months(year_end, -11).replace(day=1),
            period_end   = year_end,
            due_date     = due,
            key          = f'sat_annual:{year_end.isoformat()}',
        ))
    return out


# ── the whole calendar ───────────────────────────────────────────────────────

def build_calendar(
    start: date,
    end: date,
    *,
    vat_cycle: str = VatCycle.CATEGORY_B,
    fy_end_month: int = 6,
    vat_due_day: int = VAT_DUE_DAY,
) -> list[Obligation]:
    """Every statutory obligation with a DUE DATE inside [start, end], sorted.

    `start`/`end` filter on the due date, not the period, so an obligation whose
    period closed last year but whose deadline lands in the window is included.
    """
    obligations = (
        vat_obligations(start, end, vat_cycle, vat_due_day)
        + paye_obligations(start, end)
        + owht_obligations(start, end)
        + paye_annual_obligations(start, end, fy_end_month)
        + sat_quarterly_obligations(start, end, fy_end_month)
        + sat_annual_obligations(start, end, fy_end_month)
    )
    # `key` is unique per obligation, so this also guards against a rule that
    # generates the same filing twice from overlapping year loops.
    seen: set[str] = set()
    unique = []
    for ob in sorted(obligations, key=lambda o: (o.due_date, o.tax_type, o.key)):
        if ob.key in seen:
            continue
        seen.add(ob.key)
        unique.append(ob)
    return unique
