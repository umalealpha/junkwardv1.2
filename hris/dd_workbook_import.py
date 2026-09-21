"""
hris/dd_workbook_import.py — turn Dorothy's FY2025 Development Dialogue workbooks
into 9-box placements.

Dorothy emailed `Development Dialogues 2025.zip` on 18 Jul 2026; the 37 workbooks
landed in the HR document vault as `development_dialogue` documents. They were
never anything more than files: the 9-box grid reads `DevelopmentDialogue` rows,
of which only 7 existed, so 30 people the CFO had already reviewed were invisible
on the grid.

THE TWO AXES ARE NOT ON THE SAME SCALE. Each workbook's "Nine Box Grid" sheet
carries two totals taken straight from the PMS sheet:

    Performance Competency  → 'Overall Score (Section B)'   max 0.80
    Potential Indicator     → 'Overall Score'               max 0.20
                              'Overall Score (All Sections)' = the two, max 1.00

Bharath's workbook reads 0.7275 + 0.1970 = 0.9245, which is what pins the maxima:
the sections are weighted 80/20, not 50/50. Feeding the raw potential figure
(~0.15-0.20) into a 0..1 axis would band virtually the whole company as
low-potential — a false accusation about 35 real people's careers. So each axis is
normalised by ITS OWN maximum before it is stored.

Anything that cannot be parsed is skipped and reported, never guessed at.
"""
from __future__ import annotations

import re

# Section maxima from the PMS workbook (see module docstring).
PERFORMANCE_MAX = 0.80
POTENTIAL_MAX = 0.20

# Band thresholds as a share of a section's maximum, from the workbook's own
# "Point Scale" (10-20 / 30-40 / 50-60 / 70-80 out of 80): 70 points = 87.5% is
# Above target, 50 points = 62.5% is On target.
HIGH_FRAC = 70.0 / 80.0     # 0.875
MED_FRAC = 50.0 / 80.0      # 0.625


def _norm(s) -> str:
    return re.sub(r'\s+', ' ', str(s or '')).strip().lower()


def _num01(v):
    """A raw section total: a number between 0 and 1. Anything else is not a score."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.0001 else None


def band_normalise(raw: float, section_max: float) -> float:
    """Convert a raw section total to the 0..1 value the 9-box grid bands on.

    NOT a plain division. The grid bands in thirds (<1/3 low, <2/3 medium, else
    high), but the workbook's own scale is not linear against those thirds. Its
    "Point Scale" column runs 10-20 / 30-40 / 50-60 / 70-80 out of 80, and the Box
    Grid Guide names three performance bands — Above target, On target, Below
    target. Read together:

        70 points and up (>= 87.5% of the 80-point section)  → Above target → HIGH
        50 points and up (>= 62.5%)                          → On target    → MEDIUM
        below that                                           → Below target → LOW

    The thresholds are the POINT bands, not a share of the maximum. Reading them
    as 70%/50% of the max put 45% of the company in "Star" with nobody below Core
    Player on the first live run — a grid nobody would believe. On the point
    reading, 0.61 (61 points) is On target and 0.7275 (72.75) is Above target,
    which matches how the sheet is scored. Potential is judged the same way
    against ITS section max, since that section has no point scale of its own.

    Positions inside a band stay proportionate, so ranking within a box is kept.
    """
    if not section_max:
        return 0.0
    frac = max(0.0, min(1.0, raw / section_max))
    if frac >= HIGH_FRAC:                 # Above target → top third
        out = 2 / 3 + (frac - HIGH_FRAC) / (1.0 - HIGH_FRAC) * (1 / 3)
    elif frac >= MED_FRAC:                # On target → middle third
        out = 1 / 3 + (frac - MED_FRAC) / (HIGH_FRAC - MED_FRAC) * (1 / 3)
    else:                                 # Below target → bottom third
        out = frac / MED_FRAC * (1 / 3)
    return round(min(1.0, max(0.0, out)), 4)


def _total_under(ws, label: str, max_gap: int = 12):
    """The 'Total' figure in column B beneath the row labelled `label` in column A.

    Scans a bounded window rather than the whole sheet so a stray 'Total' further
    down a long sheet cannot be picked up as this section's score.
    """
    start = None
    target = _norm(label)
    for r in range(1, min(ws.max_row, 200) + 1):
        if _norm(ws.cell(row=r, column=1).value) == target:
            start = r
            break
    if start is None:
        return None
    for r in range(start + 1, min(start + max_gap, ws.max_row) + 1):
        if _norm(ws.cell(row=r, column=1).value) == 'total':
            return _num01(ws.cell(row=r, column=2).value)
    return None


def parse_workbook(fileobj) -> dict:
    """{performance, potential, overall, name, department, position, error}.

    `performance` / `potential` come back NORMALISED to 0..1 (each divided by its
    own section maximum) so they can be compared and banded. `overall` is the
    0-100 percentage of the combined score, matching DevelopmentDialogue.overall.
    """
    import openpyxl

    out = {'performance': None, 'potential': None, 'overall': None,
           'name': '', 'department': '', 'position': '', 'error': ''}
    try:
        wb = openpyxl.load_workbook(fileobj, data_only=True)
    except Exception as e:    # noqa: BLE001 — a corrupt upload must not stop the batch
        out['error'] = f'cannot open workbook: {type(e).__name__}'
        return out

    ws = next((w for w in wb.worksheets if 'nine box' in _norm(w.title)), None)
    if ws is None:
        out['error'] = 'no "Nine Box Grid" sheet'
        return out

    raw_perf = _total_under(ws, 'Performance Competency')
    raw_pot = _total_under(ws, 'Potential Indicator')
    if raw_perf is None and raw_pot is None:
        out['error'] = 'no performance/potential totals on the Nine Box sheet'
        return out

    # Row 2 is the header (Name / Department / Position); row 3 holds the values.
    out['name'] = str(ws.cell(row=3, column=1).value or '').strip()
    out['department'] = str(ws.cell(row=3, column=2).value or '').strip()
    out['position'] = str(ws.cell(row=3, column=3).value or '').strip()

    if raw_perf is not None:
        out['performance'] = band_normalise(raw_perf, PERFORMANCE_MAX)
    if raw_pot is not None:
        out['potential'] = band_normalise(raw_pot, POTENTIAL_MAX)
    combined = (raw_perf or 0.0) + (raw_pot or 0.0)
    out['overall'] = round(combined * 100.0, 1)
    out['raw_performance'] = raw_perf
    out['raw_potential'] = raw_pot
    return out
