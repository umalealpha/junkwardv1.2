"""claims/weekly_update_report.py — the spreadsheet and the covering email.

Separated from ``weekly_update.py`` (which only reads) so the figures can be
tested without building a workbook, and so the Monday command stays a thin
caller. Same three-file split as the failed-debits report.

The workbook is built with ``aware.reporting._wb``, the branded builder the
Aware reports already use. Imported, never copied: a second Navy/Orange table
builder drifts from the first the week somebody changes a column width, and
Finance start receiving two house styles.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Dict, List

from aware.reporting import NAVY, ORANGE, _wb

from claims.weekly_update import UNKNOWN_GROUP, UNKNOWN_MONTH

#: Column order. Group then month, because the sheet is read down a group.
COLUMNS = ['Group', 'Month reported', 'Claims', 'Reserve (BWP)', 'Payment (BWP)']

#: Columns of the unrecognised-prefix sheet section. It shares the workbook so
#: nobody can read the totals without also seeing what did not fit them.
NOTES_BASE = [
    'Built from the Graphite claims mirror in Omni on the morning it was sent '
    '— not a hand export.',
    'INCEPTION-TO-DATE. Every claim on the book is included. The month column '
    'is the month the claim was REPORTED, not the month of loss.',
    'The group is derived from the policy number prefix (COMG = Commercial, '
    'DOMG = Domestic, MIS = Miscellaneous, BONU = Bonus). Graphite has no '
    'group column.',
    'The mirror holds today\'s position only. A past week\'s version of this '
    'report cannot be reproduced from it.',
    'Omni does not move money. This report raises no payment and changes no claim.',
]


def _f(v) -> float:
    """Money as a float so Excel gets a number it can total.

    Rounded to the thebe, never truncated: a sheet whose rows do not sum to the
    headline above them is the fastest way to lose the reader's trust in all of it.
    """
    return float(Decimal(str(v or 0)).quantize(Decimal('0.01')))


def _month_label(m: str) -> str:
    if m == UNKNOWN_MONTH:
        return m
    try:
        return f'{datetime.datetime.strptime(m, "%Y-%m"):%b %Y}'
    except ValueError:  # pragma: no cover - month keys are built, not parsed
        return m


def filename(as_at: datetime.date) -> str:
    return f'Alpha Direct Weekly Claims Update {as_at:%Y-%m-%d}.xlsx'


def subject(as_at: datetime.date) -> str:
    return f'Alpha Direct weekly claims update — as at {as_at:%d %b %Y}'


def build_xlsx(summary: Dict[str, Any]):
    """The attachment. Returns a BytesIO positioned at the start."""
    as_at = summary['as_at']
    kpis = [
        ('Claims on the book', f"{summary['count']:,}"),
        ('Total reserve', f"P {_f(summary['reserve']):,.2f}"),
        ('Total paid', f"P {_f(summary['payment']):,.2f}"),
        (f"Reported in the last {summary['weeks']} week(s)", f"{summary['recent']:,}"),
        ('Position as at', f'{as_at:%d %b %Y}'),
    ]
    if summary['unrecognised_count']:
        kpis.append(('Claims whose policy prefix is not recognised',
                     f"{summary['unrecognised_count']:,}"))
    if summary['undated']:
        kpis.append(('Claims with no reported date', f"{summary['undated']:,}"))

    body: List[List[Any]] = []
    for r in summary['table']:
        body.append([r['group'], _month_label(r['month']), r['count'],
                     _f(r['reserve']), _f(r['payment'])])
    for g in summary['by_group']:
        body.append([f"Subtotal — {g['group']}", '', g['count'],
                     _f(g['reserve']), _f(g['payment'])])
    body.append(['TOTAL', '', summary['count'],
                 _f(summary['reserve']), _f(summary['payment'])])

    if summary['unrecognised']:
        # In the sheet itself, not a footnote. An unrecognised prefix is a
        # product line that may be missing from the group totals above, and a
        # reader must not be able to scroll past that.
        body.append(['', '', '', '', ''])
        body.append(['POLICY PREFIXES NOT RECOGNISED — these are NOT in any '
                     'group above', 'Example policy', 'Claims',
                     'Reserve (BWP)', 'Payment (BWP)'])
        for u in summary['unrecognised']:
            body.append([u['prefix'], u['example'], u['count'],
                         _f(u['reserve']), _f(u['payment'])])

    notes = list(NOTES_BASE)
    if summary['unrecognised']:
        notes.insert(0, 'ACTION: {} claim(s) carry a policy prefix this report '
                        'does not know ({}). They are listed at the bottom of '
                        'the table under "{}" and are NOT counted in any other '
                        'group.'.format(
                            summary['unrecognised_count'],
                            ', '.join(u['prefix'] for u in summary['unrecognised']),
                            UNKNOWN_GROUP))
    if summary['undated']:
        notes.insert(0, 'ACTION: {} claim(s) have no reported date and are '
                        'shown under "{}". They are not in any month.'.format(
                            summary['undated'], UNKNOWN_MONTH))
    if summary['no_detail']:
        notes.insert(0, 'WARNING: {} claim(s) have never had their detail '
                        'synced from Graphite, so their reserve and payment '
                        'read as zero here.'.format(summary['no_detail']))

    return _wb(
        title='Alpha Direct — Weekly Claims Update',
        subtitle=f'Inception-to-date, by month reported · as at {as_at:%d %b %Y} '
                 f'· generated by Omni',
        kpis=kpis,
        columns=COLUMNS,
        rows=body,
        notes=notes,
        # money_cols DELIBERATELY NOT PASSED. _wb formats a money column as
        # '#,##0' — whole Pula — because Aware pre-rounds. These figures are
        # thebe-accurate, so a 594.50 row would display 595 while the KPI above
        # reads P 2,072.45 and the column would visibly not add up.
    )


def build_html(summary: Dict[str, Any]) -> str:
    """The covering email: the group totals in the BODY, detail attached.

    The group split is in the body on purpose. The old hand-built version went
    round as an attachment and nobody could see at a glance whether their part
    of the book had moved.
    """
    as_at = summary['as_at']
    group_rows = ''.join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eceff5">{g["group"]}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eceff5;text-align:right">{g["count"]:,}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eceff5;text-align:right">'
        f'P {_f(g["reserve"]):,.2f}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #eceff5;text-align:right">'
        f'P {_f(g["payment"]):,.2f}</td></tr>'
        for g in summary['by_group']
    )

    flags = ''
    if summary['unrecognised']:
        listed = ', '.join(f'{u["prefix"]} ({u["count"]})'
                           for u in summary['unrecognised'])
        flags += (
            f'<p style="margin:14px 0 0;padding:10px 12px;background:#fff6ee;'
            f'border-left:4px solid #{ORANGE}">'
            f'<strong>{summary["unrecognised_count"]} claim(s)</strong> carry a '
            f'policy prefix this report does not recognise: {listed}. They are '
            f'listed separately in the attachment and are <strong>not</strong> '
            f'counted in any group above. Please tell Finance what group these '
            f'belong to so they can be mapped.</p>'
        )
    if summary['undated']:
        flags += (
            f'<p style="margin:10px 0 0;padding:10px 12px;background:#fff6ee;'
            f'border-left:4px solid #{ORANGE}">'
            f'<strong>{summary["undated"]} claim(s)</strong> have no reported '
            f'date in Graphite, so they sit in no month. They are shown under '
            f'"{UNKNOWN_MONTH}" in the attachment.</p>'
        )
    if summary['no_detail']:
        flags += (
            f'<p style="margin:10px 0 0;padding:10px 12px;background:#fff6ee;'
            f'border-left:4px solid #{ORANGE}">'
            f'<strong>{summary["no_detail"]} claim(s)</strong> have never had '
            f'their detail pulled from Graphite, so their reserve and payment '
            f'read as zero.</p>'
        )

    return f"""
<div style="font-family:Montserrat,'Segoe UI',Arial,sans-serif;color:#1D3270;font-size:14px">
  <div style="border-left:6px solid #{ORANGE};padding:2px 0 2px 14px;margin-bottom:18px">
    <div style="font-size:18px;font-weight:700">Weekly claims update</div>
    <div style="color:#667;font-size:12px">Inception-to-date, by month reported
      · position as at {as_at:%d %B %Y}</div>
  </div>

  <p style="margin:0 0 14px">
    <strong>{summary['count']:,}</strong> claims on the book, carrying
    <strong>P {_f(summary['reserve']):,.2f}</strong> of reserve and
    <strong>P {_f(summary['payment']):,.2f}</strong> paid.
    <strong>{summary['recent']:,}</strong> were reported in the last
    {summary['weeks']} week(s). The month-by-month breakdown is attached.
  </p>

  <table style="border-collapse:collapse;margin:18px 0;min-width:460px">
    <tr>
      <th style="background:#{NAVY};color:#fff;padding:8px 10px;text-align:left">Group</th>
      <th style="background:#{NAVY};color:#fff;padding:8px 10px;text-align:right">Claims</th>
      <th style="background:#{NAVY};color:#fff;padding:8px 10px;text-align:right">Reserve</th>
      <th style="background:#{NAVY};color:#fff;padding:8px 10px;text-align:right">Paid</th>
    </tr>
    {group_rows}
  </table>

  {flags}

  <p style="color:#667;font-size:12px;margin-top:20px">
    Reported date decides the month — this is <strong>not</strong> a
    date-of-loss report. The group comes from the policy number prefix, because
    Graphite has no group column. Read from Omni's claims mirror, which holds
    today's position only. Omni moves no money and changes no claim.
  </p>
</div>
"""
