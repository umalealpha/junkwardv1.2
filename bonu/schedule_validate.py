"""bonu/schedule_validate.py — pre-flight checks over a stored BONU schedule.

CFO 13-Aug-2026: "the machine catches what I keep missing." Every finding on
Kutlo's file was findable by machine the moment the workbook landed. A validator
you have to remember to run is not a control; this one runs on upload AND on the
Schedule tab, so the exceptions are on screen next to the data they came from.

Each check returns a `Finding` with:

  code         — a stable slug, so a fix can be linked back to the row
  severity     — 'critical', 'high' or 'low'; a screen colours by severity, and
                 an upload will refuse to publish over a 'critical'
  title        — one line for a person
  detail       — what the machine actually saw
  where        — sheet key + amount column, so the frontend can point at a tab

Nothing here writes to the database. The point is to READ the schedule the way
Finance reads it and surface the disagreements they would find by hand.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Optional

from .models import BonuScheduleSheet
from .schedule import _money, is_total_row, sheet_total, stated_total


# ---------------------------------------------------------------------- data --

@dataclass
class Finding:
    code: str
    severity: str
    title: str
    detail: str
    where: dict = field(default_factory=dict)
    amount: Optional[str] = None            # money at stake, when there is one

    def as_dict(self) -> dict:
        return asdict(self)


CRITICAL, HIGH, LOW = 'critical', 'high', 'low'


# ---------------------------------------------------------- small utilities --

def _col(sheet: BonuScheduleSheet, *needles) -> str:
    """First column name whose lowercase contains ALL the needle-tuple's words."""
    for w in needles:
        wants = (w,) if isinstance(w, str) else w
        for c in sheet.columns:
            cl = c.lower()
            if all(n.lower() in cl for n in wants):
                return c
    return ''


def _summary_row(sheet: BonuScheduleSheet, label: str):
    """First data row on the SUMMARY tab whose label column matches (case-fold)."""
    if not sheet.columns:
        return None
    lc = sheet.columns[0]
    label = label.strip().lower()
    for r in sheet.rows.all():
        if str(r.cells.get(lc, '')).strip().lower() == label:
            return r
    return None


def _period_columns(sheet: BonuScheduleSheet) -> list[str]:
    """Every column on SUMMARY that carries a monthly figure — between the label
    and the Totals cell.

    Kutlo's file has scratch cells to the RIGHT of Totals (Column 21..31 in his
    workbook, holding working notes like 9.222220049263495 that are not part of
    the row's period series). Including them made every firm-by-firm row read as
    a mismatch, on a screen showing 57 critical findings that were all noise.

    Order preserved from the workbook."""
    out = []
    for c in sheet.columns[1:]:
        if c.strip().lower() in ('totals', 'total'):
            break                                       # stop at the Totals cell
        out.append(c)
    return out


# ---------------------------------------------------------------- checks 1-8 --

def check_row_totals(sheet: BonuScheduleSheet) -> list[Finding]:
    """A money row's monthly cells must add up to the workbook's own Totals cell.

    Applies to the summary MATRIX tab — label + period columns + a Totals column
    — because that shape is where an over-typed total silently reshapes every
    ratio. A list sheet like UNPAID FEES also carries a Totals column but its
    other cells are metadata (bank, KYC status), not periods, so summing them
    would be meaningless.

    On Kutlo's file this caught Premiums(- VAT & Commission) where the total was
    **6,499,670.08** off the months — every ratio on the tab divided by the
    wrong base."""
    if sheet.key != 'summary':
        return []
    if 'totals' not in {c.strip().lower() for c in sheet.columns}:
        return []
    label_col = sheet.columns[0]
    totals_col = next(c for c in sheet.columns if c.strip().lower() == 'totals')
    periods = _period_columns(sheet)
    out: list[Finding] = []
    for r in sheet.rows.all():
        stated = _money(r.cells.get(totals_col))
        if stated == 0 and not any(r.cells.get(p) for p in periods):
            continue                                        # blank row
        summed = sum((_money(r.cells.get(p)) for p in periods), Decimal('0'))
        if stated == 0 and summed == 0:
            continue
        # Ratios on a matrix P&L are NOT sums, so their monthly cells will never
        # add to the total. Skip anything whose label reads "ratio" — the check
        # is for money rows.
        if 'ratio' in str(r.cells.get(label_col, '')).lower():
            continue
        if abs(summed - stated) > Decimal('0.02'):
            label = r.cells.get(label_col, f'row {r.position + 1}')
            out.append(Finding(
                code='ROW_TOTAL_MISMATCH',
                severity=CRITICAL,
                title=f'{label}: months add to {summed} but the Totals cell says {stated}',
                detail=(f'The 18 monthly cells on this row add to {summed}, but the '
                        f'Totals cell reads {stated} — off by {(summed - stated)}. '
                        f'Whichever is right, one of them is wrong.'),
                where={'sheet': sheet.key, 'row': r.position, 'label': str(label)},
                amount=str((summed - stated).quantize(Decimal('0.01'))),
            ))
    return out


def check_summary_matches_detail(summary: BonuScheduleSheet) -> list[Finding]:
    """Each summary line must equal the detail tab's total.

    * Client Claims       vs CLAIMS
    * Admin Expenses      vs ADMIN EXPENSES
    * Total Revenue       vs PREMIUMS
    """
    if not summary or summary.key != 'summary':
        return []
    label_col = summary.columns[0] if summary.columns else ''
    # (summary label, detail key, sign to expect on summary — claims/admin sit as
    # negatives on the P&L, income sits as a positive.)
    pairs = [
        ('Client Claims',   'claims',         -1),
        ('Admin Expenses',  'admin-expenses', -1),
        ('Total Revenue',   'premiums',       +1),
    ]
    out: list[Finding] = []
    for label, key, sign in pairs:
        detail = BonuScheduleSheet.objects.filter(key=key).first()
        if not detail:
            continue
        row = _summary_row(summary, label)
        if row is None:
            continue
        totals_col = next((c for c in summary.columns
                           if c.strip().lower() == 'totals'), '')
        stated = _money(row.cells.get(totals_col)) if totals_col else Decimal('0')
        # Detail totals are always positive; apply the sign the P&L uses.
        detail_total = sheet_total(detail) * sign
        gap = (detail_total - stated).quantize(Decimal('0.01'))
        if abs(gap) > Decimal('0.02'):
            out.append(Finding(
                code='SUMMARY_MISMATCH',
                severity=HIGH,
                title=f'{label}: P&L says {stated}, {detail.title} tab totals {detail_total}',
                detail=(f'The {detail.title} tab totals {detail_total} but the '
                        f'P&L line for "{label}" reads {stated} — off by {gap}. '
                        f'One of the two is not being carried through correctly.'),
                where={'summary_label': label, 'detail_key': key,
                       'summary_value': str(stated), 'detail_value': str(detail_total)},
                amount=str(gap),
            ))
    return out


def check_ratio_base(summary: BonuScheduleSheet) -> list[Finding]:
    """The base used by loss/expense/combined ratios must be revenue less VAT less
    commission — not any other number. This is the one that made Kutlo's ratios
    read 66% when the truth was 116%."""
    if not summary or summary.key != 'summary':
        return []
    labels = ('Premiums(- VAT & Commission)', 'Premiums(-VAT & Commission)',
              'Net Premium')
    row = next((_summary_row(summary, l) for l in labels
                if _summary_row(summary, l) is not None), None)
    if row is None:
        return []
    rev = _summary_row(summary, 'Total Revenue')
    vat = _summary_row(summary, 'Vat @ 14%')
    com = _summary_row(summary, 'Commission @ 15%')
    if None in (rev, vat, com):
        return []
    totals_col = next((c for c in summary.columns
                       if c.strip().lower() == 'totals'), '')
    if not totals_col:
        return []
    stated = _money(row.cells.get(totals_col))
    expected = (_money(rev.cells.get(totals_col))
                + _money(vat.cells.get(totals_col))
                + _money(com.cells.get(totals_col)))
    gap = (stated - expected).quantize(Decimal('0.01'))
    if abs(gap) > Decimal('0.02'):
        return [Finding(
            code='RATIO_BASE_WRONG',
            severity=CRITICAL,
            title='Ratio base is off — every ratio on this tab is wrong',
            detail=(f'The base row reads {stated}, but revenue less VAT less '
                    f'commission is {expected} — off by {gap}. Every loss / '
                    f'expense / combined ratio on this tab divides by the wrong '
                    f'number.'),
            where={'summary_label': 'Premiums(- VAT & Commission)'},
            amount=str(gap),
        )]
    return []


def check_stray_below_totals(sheet: BonuScheduleSheet) -> list[Finding]:
    """A row of numbers sitting BELOW the workbook's own Totals line, with no
    label. On Kutlo's premiums tab this is the P316,115 that had no date, month
    or policyholder against it."""
    seen_total = False
    out: list[Finding] = []
    amt = sheet.amount_column
    if not amt:
        return []
    for r in sheet.rows.all():
        if is_total_row(r.cells, sheet.columns):
            seen_total = True
            continue
        if not seen_total:
            continue
        val = _money(r.cells.get(amt))
        if val == 0:
            continue
        first_col = sheet.columns[0] if sheet.columns else ''
        label = str(r.cells.get(first_col, '')).strip()
        if label:
            continue                    # a labelled row is not "stray"
        out.append(Finding(
            code='STRAY_BELOW_TOTAL',
            severity=HIGH,
            title=f'{sheet.title}: {val} sits below the Totals row with no label',
            detail=('A row of numbers sits below the Totals line with nothing '
                    'in the first column. Either the Totals row is in the wrong '
                    'place or this row was added after and never labelled — '
                    'either way, the tab does not add up to its own total.'),
            where={'sheet': sheet.key, 'row': r.position},
            amount=str(val),
        ))
    return out


def check_firms_match(claims: BonuScheduleSheet, unpaid: BonuScheduleSheet
                      ) -> list[Finding]:
    """Every firm that appears on the claims register must also appear on the
    unpaid-fees tab (and vice versa). This is what surfaced Kenosi Junior Lewis
    — the P5,000 hole in the whole unpaid position."""
    if not claims or not unpaid:
        return []
    c_firm = _col(claims, ('law', 'firm'), 'firm')
    u_firm = _col(unpaid, ('claim', 'expenses'), ('law', 'firm'), 'firm')
    if not c_firm or not u_firm:
        return []
    normalise = lambda s: ' '.join(str(s or '').lower().split())
    claim_firms = {normalise(r.cells.get(c_firm)) for r in claims.rows.all()}
    claim_firms.discard('')
    unpaid_firms = {normalise(r.cells.get(u_firm)) for r in unpaid.rows.all()}
    unpaid_firms.discard('')
    out: list[Finding] = []
    for f in sorted(claim_firms - unpaid_firms):
        out.append(Finding(
            code='FIRM_MISSING_FROM_UNPAID',
            severity=HIGH,
            title=f'{f.title()} is on the claims list but not on unpaid fees',
            detail=('Every firm on the claims list should appear on the unpaid-'
                    'fees tab (even with a zero, so the reader knows it was '
                    'considered). This one is missing.'),
            where={'firm': f.title(), 'sheet': 'unpaid-fees'},
        ))
    return out


def check_missing_dates(claims: BonuScheduleSheet) -> list[Finding]:
    """Fee notes with no usable invoice date. On Kutlo's file, 1,031 of 1,169."""
    if not claims:
        return []
    date_col = _col(claims, ('inv', 'date'), 'date')
    if not date_col:
        return []
    total = missing = 0
    for r in claims.rows.all():
        if not r.cells.get(claims.amount_column):
            continue
        total += 1
        d = str(r.cells.get(date_col, '')).strip()
        if not d or d.lower() in ('none', '-'):
            missing += 1
    if total == 0 or missing == 0:
        return []
    sev = HIGH if missing / total > 0.05 else LOW
    return [Finding(
        code='MISSING_INVOICE_DATE',
        severity=sev,
        title=f'{missing} of {total} fee notes carry no invoice date',
        detail=('Without an invoice date the row cannot be aged or grouped by '
                'month, so SLA, interest exposure and monthly reconciliation '
                'cannot be run against it.'),
        where={'sheet': 'claims', 'column': date_col},
    )]


def check_missing_amounts(claims: BonuScheduleSheet) -> list[Finding]:
    """Fee notes with no amount at all."""
    if not claims or not claims.amount_column:
        return []
    missing = sum(1 for r in claims.rows.all()
                  if not str(r.cells.get(claims.amount_column, '')).strip())
    if missing == 0:
        return []
    return [Finding(
        code='MISSING_AMOUNT',
        severity=HIGH,
        title=f'{missing} fee notes carry no amount',
        detail=('A fee note without an amount cannot be paid, aged or reported.'),
        where={'sheet': 'claims', 'column': claims.amount_column},
    )]


def check_status_values(claims: BonuScheduleSheet) -> list[Finding]:
    """Two values for the same thing — 'Paid' and 'Paid ' with a trailing space
    are the same status on paper, two separate values in the system, and every
    aggregate that groups by status silently splits them."""
    if not claims:
        return []
    status_col = _col(claims, ('status',), ('payment', 'status'))
    if not status_col:
        return []
    values = Counter(str(r.cells.get(status_col, '') or '').strip(' ')
                     for r in claims.rows.all())
    seen = {}
    dupes: list[tuple[str, str, int, int]] = []
    for v, n in values.items():
        key = v.strip().lower()
        if not key:
            continue
        if key in seen:
            other, other_n = seen[key]
            dupes.append((other, v, other_n, n))
        else:
            seen[key] = (v, n)
    if not dupes:
        return []
    lines = [f'"{a}" ({na}) and "{b}" ({nb})' for a, b, na, nb in dupes]
    return [Finding(
        code='STATUS_SPACING',
        severity=LOW,
        title=f'{len(dupes)} status value(s) appear in two spellings each',
        detail=('The same status is stored under two spellings — trailing spaces '
                'and casing — so groupings split them. ' + '; '.join(lines) + '.'),
        where={'sheet': 'claims', 'column': status_col},
    )]


# ---------------------------------------------------------------------- api --

def run_all() -> dict:
    """Run every check over the currently loaded schedule."""
    findings: list[Finding] = []
    for sh in BonuScheduleSheet.objects.all():
        findings.extend(check_row_totals(sh))
        findings.extend(check_stray_below_totals(sh))
    summary = BonuScheduleSheet.objects.filter(key='summary').first()
    findings.extend(check_summary_matches_detail(summary))
    findings.extend(check_ratio_base(summary))
    claims = BonuScheduleSheet.objects.filter(key='claims').first()
    unpaid = BonuScheduleSheet.objects.filter(key='unpaid-fees').first()
    findings.extend(check_firms_match(claims, unpaid))
    findings.extend(check_missing_dates(claims))
    findings.extend(check_missing_amounts(claims))
    findings.extend(check_status_values(claims))
    return summarise(findings)


def summarise(findings: list[Finding]) -> dict:
    order = {CRITICAL: 0, HIGH: 1, LOW: 2}
    findings = sorted(findings, key=lambda f: (order[f.severity], f.code))
    counts = Counter(f.severity for f in findings)
    return {
        'critical_count': counts.get(CRITICAL, 0),
        'high_count':     counts.get(HIGH, 0),
        'low_count':      counts.get(LOW, 0),
        'findings':       [f.as_dict() for f in findings],
    }
