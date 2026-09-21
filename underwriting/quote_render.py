"""underwriting/quote_render.py — the quotation as an A4 PDF.

Renders `templates/underwriting/quote.html` through the same headless Chromium
the cover notes use, so the stored copy is pixel-identical to what the
underwriter saw on screen.

The money in the PDF comes from `quote_parse.price()` via the model, never from
the caller — so the printed VAT cannot disagree with the VAT on the record.
"""
from __future__ import annotations

import base64
import logging
import re
import threading
from decimal import Decimal
from functools import lru_cache
from html import escape
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

from .render import RenderUnavailable

log = logging.getLogger(__name__)

# Chromium is ~150-300 MB per launch; cap concurrency exactly as render.py does
# so a burst of issues cannot exhaust the box.
_SEM = threading.BoundedSemaphore(
    int(getattr(settings, 'UNDERWRITING_MAX_CONCURRENT_RENDERS', 2)))


# The quotation is a CLIENT document. How the risk is laid off is ours and the
# reinsurer's business, and the client does not need to know it (CFO 2026-08-08).
# The underwriter's note routinely carries it — "fac placed 60% with the treaty",
# "we retain 5m" — because that is how they think about the risk while they price
# it, and everything in the note flows through Aria into the cover rows and onto
# the PDF. So it is stopped here, at the document, where nothing gets past it:
# what the underwriter typed is kept on the record, it just never prints.
_REINSURANCE = re.compile(
    r'\b(re-?insur\w*|reinsurer|facultativ\w*|\bfac\b|treaty|treaties|cede[ds]?|'
    r'cession|retrocess\w*|retention|quota[- ]share|surplus\s+treaty|'
    r'excess\s+of\s+loss|\bxo?l\b|\bri\b)\b',
    re.I)


def _hide_reinsurance(rows):
    """Drop the reinsurance out of what the client reads.

    A whole row that is ABOUT reinsurance goes; a row that merely mentions it in
    its note keeps the cover and loses the note. Cover the client bought is never
    removed — only the arrangement behind it.
    """
    kept = []
    for row in rows:
        name = f"{row.get('group', '')} {row.get('name', '')}"
        if _REINSURANCE.search(name):
            continue
        if _REINSURANCE.search(str(row.get('note') or '')):
            row = {**row, 'note': ''}
        # The per-cover notes box invites conditions/warranties text, so an
        # underwriter could type the reinsurance arrangement into it ("60% fac
        # placed with the treaty"). Scrub it the same way as the per-row note —
        # kept on the record, never printed on the client document (CFO 2026-08-08).
        if _REINSURANCE.search(str(row.get('section_note') or '')):
            row = {**row, 'section_note': ''}
        kept.append(row)
    return kept


# "Workman's", "Workmens", "Workmen's" — underwriters type whichever they were
# taught, and three spellings of one section in one schedule reads as
# carelessness to a broker (CFO 2026-08-11).
_WORKMEN = re.compile(r"\bworkm[ae]n'?s\b", re.I)


def _one_spelling(text: str) -> str:
    """One spelling of Workmen's across the document, keeping the typed case.

    Three cases, because the schedule really does carry all three: an all-caps
    section heading stays all-caps — "WORKMAN'S COMPENSATION" was coming back as
    "Workmen's COMPENSATION", half title-case half shouting, on the printed
    summary (CFO 2026-08-11).
    """
    def fix(m):
        w = m.group(0)
        if w.isupper():
            return "WORKMEN'S"
        return "Workmen's" if w[:1].isupper() else "workmen's"
    return _WORKMEN.sub(fix, str(text or ''))


# A section's qualifier — the bit after the dash in "Fire — premises: Tribal Lot
# 181". What comes before it is the family the section belongs to.
_QUALIFIER = re.compile(r'\s+[—–]\s+|\s+-\s+')


def _family(label: str) -> str:
    """"Fire", "Fire — premises: Oodi" and "Fire — premises: Lot 181" → "fire"."""
    return _QUALIFIER.split(label, 1)[0].strip().casefold()


# Per-vehicle sub-limits. Graphite stores each vehicle as FOUR rows — the
# vehicle, then third-party / windscreen / loss-of-keys — so a 15-vehicle fleet
# printed 45 sub-limit rows and the document ran to six pages. These fold into
# ONE muted sub-line under their vehicle (CFO fix list, 11 Aug 2026 #5).
_VEHICLE_SUBS = {'third party liability', 'windscreen', 'loss of keys'}


def _sectioned_rows(rows):
    """Lay the printed rows out as sections: one bar per section, excess once.

    Format faults on the LUCIENT quote, all fixed here (CFO 2026-08-11):

      * the same section heading appeared more than once, its rows scattered
        down the schedule — three separate Fire blocks in different places;
      * a section with ONE excess repeated that excess on every row, so 24 motor
        rows carried the same sentence and the column had to be wide enough to
        hold it. It is a property of the section, so it is stated once on the
        section bar and applies to every item under it;
      * a vehicle's third-party / windscreen / loss-of-keys rows printed as
        three data rows EACH — they fold into one sub-line under the vehicle,
        and when they carry no values at all (the unvalued Prado) they vanish;
      * a row with no sum insured, no rate and no premium is not cover — it is
        a remark ("Occupation surcharge: nil", "Claim basis: claims occurring")
        that printed as a line of dashes. Its words move onto the section bar
        and the dash row goes.

    Rows keep their order WITHIN a section and sections keep the order they
    first appear — the underwriter's sequence is never re-sorted, only gathered.
    Where one row's excess DIFFERS from the rest of its section it stays on that
    row: a wrong excess on a client document is worse than a repeated one.
    """
    order, buckets, last = [], {}, ''
    for row in rows:
        label = str(row.get('group') or '').strip()
        if label:
            last = label
        label = last
        if label not in buckets:
            order.append(label)
            buckets[label] = []
        buckets[label].append(row)

    # Sections of one FAMILY print together. The LUCIENT schedule carried three
    # Fire sections in three different places with Office Contents in between, so
    # the reader met "Fire" three times down the document and could not see what
    # the fire cover came to (CFO 2026-08-11). Families keep the order they first
    # appear and so do the sections inside a family — nothing is re-sorted, the
    # related blocks are only gathered.
    fam_order, by_family = [], {}
    for label in order:
        fam = _family(label)
        if fam not in by_family:
            fam_order.append(fam)
            by_family[fam] = []
        by_family[fam].append(label)
    order = [label for fam in fam_order for label in by_family[fam]]

    out = []
    for label in order:
        members = buckets[label]
        kept, remarks = [], []
        for idx, r in enumerate(members):
            r = dict(r)
            name = str(r.get('name') or '').strip()
            raw_sum = str(r.get('sum_insured') or '').strip()
            raw_rate = str(r.get('rate') or '').strip()
            raw_prem = str(r.get('premium') or '').strip()
            priced = bool(raw_rate or raw_prem)
            # A vehicle sub-limit folds into the vehicle above it; one that has
            # no value at all (the unvalued Prado's) simply goes. A PRICED row
            # is never folded, whatever it is called — money must stay visible.
            if name.casefold().rstrip(':') in _VEHICLE_SUBS and kept and not priced:
                if raw_sum:
                    shown = raw_sum[:-3] if raw_sum.endswith('.00') else raw_sum
                    kept[-1].setdefault('_subl', []).append(f'{name} {shown}')
                continue
            if not raw_sum and not priced:
                nxt = members[idx + 1] if idx + 1 < len(members) else {}
                nxt_name = str((nxt or {}).get('name') or '').strip().casefold().rstrip(':')
                is_vehicle = nxt_name in _VEHICLE_SUBS
                # A no-value row FOLLOWED BY its own sub-limit rows is an item
                # still awaiting its figures — the Prado, whose third-party /
                # windscreen / loss-of-keys rows follow it. It stays visible so
                # the client sees the cover is unresolved; prod's data carries no
                # note on it, so one is supplied (fix, 11 Aug 2026 — the Prado
                # was being swallowed onto the section bar). A row that carries
                # its own note is likewise a real item and stays.
                if is_vehicle or str(r.get('note') or '').strip():
                    if not str(r.get('note') or '').strip():
                        r['note'] = 'Sum insured and premium to be confirmed — refer to underwriter'
                    kept.append(r)
                    continue
                # Otherwise it is a remark ("Occupation surcharge: nil", "Claim
                # basis: claims occurring" — fix list #4): its words go on the bar.
                own = str(r.get('excess') or '').strip()
                text = f'{name}: {own}' if (name and own) else (name or own)
                if text and label:
                    remarks.append(text)
                continue
            kept.append(r)

        # Blank rows don't vote: a vehicle's sub-limit rows carry no excess of
        # their own, and counting their '' as "a different excess" kept the one
        # real excess printing on all 24 motor rows — the exact repetition this
        # function exists to stop (caught on the printed LUCIENT PDF, 11 Aug).
        excesses = {e for e in (str(r.get('excess') or '').strip() for r in kept) if e}
        # Only a real section has a bar to print the excess on; ungrouped rows
        # keep theirs on the row or it would vanish off the document.
        shared = (excesses.pop() if len(excesses) == 1 else '') if label else ''
        # Everything the section states once, on its bar: the excess, then the
        # remark rows in the order they were typed.
        bar = ' · '.join(([f'Excess: {shared}'] if shared else []) + remarks)
        # Per-section notes (Underwriting, 17 Aug 2026): a note the
        # underwriter attaches to a WHOLE cover/section (benefits, exclusions,
        # extensions, conditions, warranties). Printed once, under this
        # section's last row — kept separate from the quote's general Notes
        # block. Collect EVERY distinct note across the section's rows: same-
        # label sections merge into one bucket (the LUCIENT three-"Fire" case),
        # so a second underwriter-typed note must NOT be silently dropped.
        seen_notes, section_notes = set(), []
        for m in members:
            n = str(m.get('section_note') or '').strip()
            if n and n not in seen_notes:
                seen_notes.add(n)
                section_notes.append(n)
        # The note is authored on the section's heading row, so the copies in
        # `kept` still carry it — strip it from every row so it can't print on
        # each one, then place them ONCE, joined, on the section's last visible
        # row.
        for r in kept:
            r.pop('section_note', None)
        if kept and section_notes:
            kept[-1]['section_note'] = '\n'.join(section_notes)
        for i, r in enumerate(kept):
            # The first row of a section draws the bar and carries its info.
            r['group'] = label if i == 0 else ''
            r['group_info'] = bar if i == 0 else ''
            own = str(r.get('excess') or '').strip()
            r['row_excess'] = '' if shared else own
            # The BASIS column stood empty on every row of a real quotation, so
            # it is gone; where an underwriter does state a basis it reads as
            # part of the item instead of holding a column open for it. Folded
            # vehicle sub-limits lead the line.
            r['sub'] = ' · '.join(x for x in (*r.pop('_subl', []),
                                              str(r.get('basis') or '').strip(),
                                              str(r.get('note') or '').strip()) if x)
            out.append(r)
    return out


# Common Law Liability on a Workmen's Compensation cover (Underwriting,
# 19 Aug 2026 — feature request from /underwriting/quotes: "Common Law Liability
# 1 000 000,00 (should always be there)").
#
# Every other benefit she listed — Death, Permanent Total Disablement, Temporary
# Total Disablement, Medical Expenses — already prints, because the underwriter
# types those rows on the schedule. Common Law Liability is the one that is
# standard on every WCA cover and therefore the one most easily forgotten, and a
# quote that omits it understates the cover being sold.
#
# So it is guaranteed here rather than left to be typed: print-side only. The
# premium is calculated from `quote.sections` by section_premium_rows(), which
# this function does not touch, so adding the line cannot move a single thebe of
# the premium — exactly like the Death and PTD rows, which carry a limit and no
# rate of their own.
#: Underwriting's standard limit, in one named place.
#:
#: Reviewed twice. A settings override was tried and taken back out: it was never
#: asked for, it would have needed wiring through compose to have any effect at all,
#: and a mistyped value would have silently printed this figure anyway — a knob that
#: looks adjustable and is not is worse than a constant. The escape hatch already
#: exists per quote: an underwriter who types their own Common Law Liability row
#: keeps their own figure, and this is not added on top.
_CLL_LABEL = 'Common Law Liability'
_CLL_SUM_INSURED = Decimal('1000000')


def _is_workmens(row) -> bool:
    """Does this row belong to a Workmen's Compensation section?

    Uses the same spelling-agnostic pattern as the rest of the document, so
    "Workman's", "Workmens" and "WORKMEN'S COMPENSATION" all count.
    """
    for field in ('group', 'name', 'item'):
        if _WORKMEN.search(str(row.get(field) or '')):
            return True
    return False


def _ensure_common_law_liability(rows):
    """Return the rows with one Common Law Liability row per Workmen's section.

    Builds a NEW list rather than inserting into the one it was handed. The list it
    receives is local to _display_rows, so mutating it was safe — but "safe because
    of where it is called from" is a property that survives exactly until someone
    calls it from somewhere else (raised on review).

    Section membership is decided on the INHERITED group, the same way
    _sectioned_rows decides it: only the first row of a section carries the label,
    and every row after it carries `group: ''` until the next label. The frontend's
    add-row default is a blank group, so keying on the row's own fields found the
    heading row and missed every benefit line beneath it — a Common Law Liability
    the underwriter had typed went undetected and a second one was added, putting
    two contradictory liability figures on one client document (Fable review).
    """
    if not rows:
        return rows
    # Walk once, resolving each row to its section exactly as the printer will.
    inherited, last = [], ''
    for r in rows:
        label = str(r.get('group') or '').strip()
        if label:
            last = label
        inherited.append(last)

    sections = {}
    for i, (r, label) in enumerate(zip(rows, inherited)):
        if not _WORKMEN.search(label) and not _is_workmens(r):
            continue
        fam = _family(label) or _family(str(r.get('name') or ''))
        info = sections.setdefault(fam, {'last': i, 'has_cll': False})
        info['last'] = i
        text = ' '.join(str(r.get(f) or '') for f in ('name', 'item', 'note'))
        if 'common law' in text.lower():
            info['has_cll'] = True

    add_after = {info['last'] for info in sections.values() if not info['has_cll']}
    if not add_after:
        return rows

    out = []
    for i, r in enumerate(rows):
        out.append(r)
        if i not in add_after:
            continue
        out.append({
            'group':       '',      # a benefit of the section above, not a new one
            'name':        _CLL_LABEL,
            # Formatted here: this row is added after the formatting pass above,
            # so a raw Decimal would print as "1000000" beside "1,049,500.00".
            'sum_insured': f'{_CLL_SUM_INSURED:,.2f}',
            'rate':        '',
            'premium':     '',
            'note':        '',
            'basis':       '',
            'excess':      '',
            '_src':        r.get('_src'),
            '_cll_added':  True,    # so the totals and the exports can find it
        })
    return out


def sections_with_guaranteed_rows(sections):
    """[(original index or None, row)] — the record's rows plus the guaranteed ones.

    For the OTHER builders of the same quote (the Excel and Word exports), which
    iterate the record directly. Both map a premium onto a row by its position in
    `quote.sections`, so inserting a row into that list would slide every premium
    after it onto the wrong line — far worse than the missing line. The original
    index travels with each row instead, and a guaranteed row carries None, which
    misses the premium lookup exactly as it should: it is a limit, not a priced item.

    Without this, the PDF carried the guaranteed benefit and the Excel and Word
    copies did not — and quote_export's own docstring promises all three agree
    (Fable review).
    """
    src = [s for s in (sections or []) if isinstance(s, dict)]
    pairs = [(i, r) for i, r in enumerate(src)]
    augmented = _ensure_common_law_liability([dict(r) for r in src])
    out, cursor = [], 0
    for row in augmented:
        if row.get('_cll_added'):
            out.append((None, row))
            continue
        out.append((pairs[cursor][0], pairs[cursor][1]))
        cursor += 1
    return out


def _display_rows(sections):
    """Print figures as figures. The underwriter types "8m" because that is how
    they say it on the phone, and it stayed "8m" in a column headed SUM INSURED
    (BWP) on a document going out to a broker. Anything that parses as a number is
    formatted; wording like "Included" or "As per policy" is left exactly as typed.
    """
    from .quote_parse import _to_decimal
    out = []
    for i, s in enumerate(sections or []):
        row = dict(s)
        # Keep the source position so a per-row premium can be matched back onto
        # the row AS PRINTED, even after reinsurance rows are dropped.
        row['_src'] = i
        for field in ('group', 'name', 'note', 'excess', 'section_note'):
            if row.get(field):
                row[field] = _one_spelling(row[field])
        for field in ('sum_insured', 'excess'):
            raw = str(row.get(field) or '').strip()
            if not raw:
                continue
            amount = _to_decimal(raw)
            if amount is not None and amount > 0:
                row[field] = f'{amount:,.2f}'
            else:
                # Wording, not money ("Included", "As per policy"). Flagged so the
                # template reads it as words instead of right-aligning it in a
                # monospace figures column, where it looks like a broken number.
                row[f'{field}_is_text'] = True
        out.append(row)
    return _sectioned_rows(_hide_reinsurance(_ensure_common_law_liability(out)))


# ── brand assets ─────────────────────────────────────────────────────────────
# The clean PNGs already used by the PO print. A quotation is a client document
# and must carry the real mark, not a typed company name (CFO 2026-08-10) — and
# the mark used is the FULL-COLOUR one on a white letterhead, untouched. The
# white monotone version was only needed while the page opened on a navy band;
# the CFO's format (11 Aug 2026) is white at the top with the logo as it is.
# Anything missing lives in the Google Drive "Logo & Signature" folder.
_BRAND_DIR = Path(__file__).resolve().parent.parent
_LOGO_COLOUR = _BRAND_DIR / 'frontend' / 'public' / 'brand' / 'logo-full-color.png'
# White monotone mark for the NAVY masthead (CFO 2026-08-13: the quotation
# reverts to the navy-band + orange-title-bar of the earlier signed layout —
# the colour mark would disappear on navy, so the white one is used up top).
_LOGO_WHITE = _BRAND_DIR / 'frontend' / 'public' / 'brand' / 'logo-monotone-white.png'
_STAMP = _BRAND_DIR / 'procurement' / 'pdf_assets' / 'stamp.png'

# ── the brand typefaces, carried INSIDE the document ─────────────────────────
# The render box has only the basic free fonts (Liberation, DejaVu), so the
# quotation printed there came out looking plain whatever the CSS asked for —
# the reason the LUCIENT document still did not read as premium after two passes
# (CFO handover, 11 Aug 2026). Both faces now travel in the HTML as base64, so
# the PDF is typeset in the brand's own faces on any box, with no network call
# at render time and nothing to install in the image. Montserrat for the
# masthead and headings, Open Sans for text and figures — the org brand pairing.
# Variable fonts, so one file per family covers weights 400-800.
# SIL Open Font License 1.1; the licence text ships beside the files.
_FONT_DIR = Path(__file__).resolve().parent / 'pdf_assets' / 'fonts'
_FONT_DISPLAY = _FONT_DIR / 'Montserrat-latin-var.woff2'
_FONT_TEXT = _FONT_DIR / 'OpenSans-latin-var.woff2'


@lru_cache(maxsize=4)
def _woff2_data_uri(path_str: str) -> str:
    """A woff2 face as an inline data URI, or '' if the file is missing.

    Missing means the document falls back to the plain stack rather than failing
    to render — but tests_quote asserts both faces exist, so CI goes red if one
    is moved or dropped from the image.
    """
    p = Path(path_str)
    if not p.exists():
        log.warning('quote font missing, document will print in a fallback face: %s', p.name)
        return ''
    return 'data:font/woff2;base64,' + base64.b64encode(p.read_bytes()).decode('ascii')


@lru_cache(maxsize=4)
def _png_data_uri(path_str: str) -> str:
    """A PNG as an inline data URI, or '' if the asset is missing. Cached: the
    same three images are re-read for every quotation otherwise."""
    p = Path(path_str)
    if not p.exists():
        # Never fail the render for a missing image — an underwriter losing the
        # ability to quote is worse than an unbranded page. But do NOT let it pass
        # quietly: the masthead falls back to typed text and nobody would know.
        # tests_quote asserts these assets exist, so CI goes red if one moves.
        log.warning('quote brand asset missing, document will render without it: %s', p.name)
        return ''
    return 'data:image/png;base64,' + base64.b64encode(p.read_bytes()).decode('ascii')


def verify_url(quote) -> str:
    """The public page the QR code opens. The quote's UUID is the capability
    token — unguessable, so only someone holding the document can reach it."""
    base = str(getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw')).rstrip('/')
    return f'{base}/api/verify/quote/{quote.pk}/'


def qr_svg_data_uri(url: str, size: int = 132) -> str:
    """A QR code as an inline SVG data URI — vector, so it prints crisp at any size.

    Built from reportlab's QR matrix (reportlab is already a dependency; the
    `qrcode` package is not installed on the box) and emitted as ONE <path>.
    reportlab's own renderSVG writes a separate <rect> per module — 93 KB for this
    URL, which bloats every stored quotation; the path form is a couple of KB.
    Error correction H, matching the PO code, so it still scans if the print is
    marked or partly covered.
    """
    from reportlab.graphics.barcode import qr
    widget = qr.QrCodeWidget(url, barLevel='H')
    # The matrix is built LAZILY — reading widget.qr.modules straight after
    # construction returns None (it did, and the render blew up). getBounds()
    # forces the encode first.
    widget.getBounds()
    matrix = getattr(widget.qr, 'modules', None)
    if not matrix:
        return ''
    n = len(matrix)
    # 4-module quiet zone each side, per the QR spec — without it scanners fail.
    quiet = 4
    span = n + quiet * 2
    parts = []
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                parts.append(f'M{x + quiet} {y + quiet}h1v1h-1z')
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {span} {span}" shape-rendering="crispEdges">'
        f'<rect width="{span}" height="{span}" fill="#fff"/>'
        f'<path d="{"".join(parts)}" fill="#1D3270"/></svg>'
    )
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode('utf-8')).decode('ascii')


def _to_decimal_safe(raw):
    from .quote_parse import _to_decimal
    return _to_decimal(raw)


def _rows_with_premiums(quote):
    """The printed cover rows, each carrying its own net premium, plus the group
    subtotals and the grand net — the client-facing breakdown.

    The figures come from quote_parse.section_premium_rows, the SAME function the
    model uses in recalc() to set the stored premium, so the breakdown on the page
    and the premium on the record cannot disagree.
    """
    from .quote_parse import section_premium_rows
    rows = _display_rows(quote.sections)
    breakdown = section_premium_rows(quote.sections, quote.rate_incl_vat)
    if not breakdown:
        return rows, None

    # A reinsurance row is hidden from the client (_hide_reinsurance) but is still
    # priced into the premium, so if one carries money the printed column would not
    # add up to the printed total — in front of a broker. Fall back to the single
    # figure rather than print a breakdown that fails to reconcile. The stored
    # premium is unchanged, so record and document still agree.
    visible = {r.get('_src') for r in rows}
    if any(p['index'] not in visible for p in breakdown['rows']):
        return rows, None

    # Map net premiums back onto the rows AS PRINTED by SOURCE POSITION — the
    # figures have already been comma-formatted for display, so matching on their
    # text would silently never hit (it didn't: every row printed "—" while the
    # totals were right, caught in QC 2026-08-10).
    net_by_src = {p['index']: p['net'] for p in breakdown['rows']}
    for row in rows:
        # Show the rate that produced the premium, so the client can see HOW it was
        # arrived at (CFO 2026-08-10). A flat-premium row has no rate — print "—"
        # rather than imply a percentage that was never applied.
        rate = _to_decimal_safe(row.get('rate'))
        if rate is not None and rate > 0:
            # ALWAYS two decimals: "4%" printed next to "4.50%" reads as two
            # different kinds of figure on a client document (CFO 2026-08-10).
            # 4 -> 4.00%, 4.5 -> 4.50%, 0.15 -> 0.15%.
            row['rate_display'] = f'{rate:.2f}%'
        net = net_by_src.get(row.get('_src'))
        if net is not None:
            row['premium_display'] = f'{net:,.2f}'

    # Sub-limits — products liability, common law, windscreen — are part of what
    # their section's premium buys, not items with a price of their own. Printed
    # as indented sub-lines with the money columns left EMPTY: a dash in a premium
    # column on a client document reads as "not covered" (CFO 2026-08-11).
    #
    # A sub-limit is a row that STATES A LIMIT but carries no premium, inside a
    # section that is priced. The limit is what makes it a sub-limit: a row with
    # no sum insured and no premium is a main item still waiting for its figures
    # (the Prado on the LUCIENT fleet, "to be confirmed"), and indenting that
    # under the vehicle above it would read as part of that vehicle's cover.
    blocks, block = [], []
    for row in rows:
        if row.get('group') and block:
            blocks.append(block)
            block = []
        block.append(row)
    if block:
        blocks.append(block)
    for block in blocks:
        if any(r.get('premium_display') for r in block):
            for r in block:
                if not r.get('premium_display') and str(r.get('sum_insured') or '').strip():
                    r['is_sub'] = True

    # Summary of premiums by section — the recap a client actually uses, so it is
    # ordered by what the cover COSTS, biggest first, with each section's share
    # of the premium shown as a bar. An unsorted list in schedule order made the
    # reader add up fifteen lines to find where the money went (CFO 2026-08-11).
    total_net = breakdown['net'] or Decimal('0')
    groups = []
    for g in breakdown['groups']:
        share = (g['net'] * 100 / total_net) if total_net else Decimal('0')
        groups.append({
            # Same spelling here as on the section bar — the recap read
            # "Workman's" while the schedule above it read "Workmen's".
            'group': _one_spelling(g['group']),
            'net': f"{g['net']:,.2f}",
            '_net': g['net'],
            # A section that cost real money must not read as "0.0" — that says
            # free. Below a tenth of a percent it is shown as less than that.
            'pct': '<0.1' if 0 < share < Decimal('0.05') else f'{share:.1f}',
            # A hair of width so the smallest section still shows a visible bar.
            'bar': f'{max(float(share), 0.4):.1f}',
        })
    groups.sort(key=lambda g: g['_net'], reverse=True)
    return rows, {'groups': groups, 'net': f"{breakdown['net']:,.2f}"}


def quote_html(quote, style: str = 'detailed') -> str:
    """The quotation as HTML — also what the on-screen preview shows.

    style='detailed' (the default) is the full document: every cover row, the
    exclusions, the conditions. style='simple' is the short version a client who
    only wants the price asks for — the section summary and the premium, without
    the row-by-row schedule or the exclusions (CFO 2026-08-11). Same record, same
    figures; only how much is printed changes.
    """
    from .quote_parse import VAT_RATE, instalment_plan, total_sum_insured, prorate
    rows, breakdown = _rows_with_premiums(quote)
    url = verify_url(quote)
    # `or 12` swallows a ZERO (0 is falsy) and a nil-month quote would
    # then print twelve instalments. Treat only None as 'not set'.
    _pm = getattr(quote, 'period_months', None)
    months = 12 if _pm is None else int(_pm)
    # Instalments run over the months actually on cover: a 6-month policy is six
    # payments, not twelve (CFO 2026-08-11).
    plan = instalment_plan(quote.total, months=months)
    # For a part-year quote, show the annual figure it was derived from.
    # The annual figure comes from the RECORD, never from `breakdown` (None for
    # a quote-level rate, a typed premium, or the hidden-reinsurance fallback)
    # and never from `premium` (which already carries the pro-rata cut) — either
    # made the premium table contradict the VAT and total on the same page.
    pro = prorate(quote.annual_premium or quote.premium, months) if months < 12 else None
    # Total cover — the first figure a broker looks for, and it was missing from
    # the document entirely (CFO 2026-08-10). Same function the rating uses.
    tsi = total_sum_insured(quote.sections)
    # A print-side row is invisible to the record, so the Totals cell and the
    # header tile undershot the column printed right above them by exactly the
    # guaranteed limit — in front of a broker. Add what was injected.
    tsi = tsi + sum((_CLL_SUM_INSURED for r in rows if r.get('_cll_added')),
                    Decimal('0'))
    uw = quote.underwriter
    if plan:
        # Pre-format for the page: the template must never do money arithmetic.
        for k in ('monthly_premium', 'plan_charge', 'monthly_instalment',
                  'total_over_term', 'annual_total'):
            plan[f'{k}_f'] = f'{plan[k]:,.2f}'
    return render_to_string('underwriting/quote.html', {
        'q': quote,
        'rows': rows,
        # The monthly-payment option: annual total / 12 plus the payment-plan
        # charge. Computed in quote_parse with the rest of the money, never here.
        'plan': plan,
        # Scan-to-verify: the client can confirm the document is genuinely ours.
        'verify_url': url,
        'verify_qr': qr_svg_data_uri(url),
        # Per-row premiums + group subtotals when the quote is priced row-by-row;
        # None for a single-figure quote, and the table then omits the column.
        'breakdown': breakdown,
        'simple': (style or 'detailed').lower() in ('simple', 'simplified'),
        'money': {
            'premium': f'{quote.premium:,.2f}',
            'vat': f'{quote.vat:,.2f}',
            'total': f'{quote.total:,.2f}',
            # The rate comes from the one place it is defined, never a literal —
            # otherwise the printed percentage can drift from the actual maths.
            'vat_rate_pct': str((VAT_RATE * 100).normalize()),
        },
        # Brand marks: white logo on the navy masthead, and the company stamp on
        # a quotation that has actually been issued.
        'logo': _png_data_uri(str(_LOGO_COLOUR)),
        'logo_white': _png_data_uri(str(_LOGO_WHITE)),
        'stamp': _png_data_uri(str(_STAMP)) if quote.status != quote.Status.DRAFT else '',
        # A quote that has not been issued is a DRAFT — it carries a NOT APPROVED
        # watermark so a saved-but-unissued copy (an agent's working draft) can
        # never be passed off as a firm, approved quotation (CFO 2026-08-12,
        # Motlatsi). It clears the moment the quote is issued and the stamp above
        # takes over — the two are mutually exclusive.
        'not_approved': quote.status == quote.Status.DRAFT,
        # The brand faces, embedded — see _woff2_data_uri.
        'font_display': _woff2_data_uri(str(_FONT_DISPLAY)),
        'font_text': _woff2_data_uri(str(_FONT_TEXT)),
        'total_sum_insured': f'{tsi:,.2f}' if tsi else '',
        'prorata': ({'months': pro['months'], 'fraction': pro['fraction'],
                     'annual': f"{pro['annual']:,.2f}", 'charged': f"{pro['charged']:,.2f}"}
                    if pro else None),
        'section_count': len(breakdown['groups']) if breakdown else 0,
        # Who the client should call. The name alone left them nowhere to go.
        'underwriter_email': (getattr(uw, 'email', '') or '').strip(),
        'issued_on': (quote.issued_at or quote.created_at).strftime('%d %B %Y'),
        'valid_until': quote.valid_until.strftime('%d %B %Y') if quote.valid_until else '—',
        'underwriter': (quote.underwriter.get_full_name() or quote.underwriter.username)
                       if quote.underwriter else '',
    })


def render_quote_pdf(quote, style: str = 'detailed') -> bytes:
    """A4 PDF bytes. Raises RenderUnavailable if the engine is missing or busy —
    the caller issues the quotation anyway and re-renders the copy later."""
    if not _SEM.acquire(blocking=False):
        raise RenderUnavailable('Renderer busy — try again in a moment.')
    try:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RenderUnavailable('Browser engine not installed.') from exc

        html = quote_html(quote, style=style)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until='load')
                # The embedded faces decode asynchronously, and `load` does not
                # wait for them — take the PDF a moment early and the whole
                # document prints in the fallback face while the CSS looks right.
                # Never let that fail the render: a plainer PDF beats no PDF.
                try:
                    page.evaluate('() => document.fonts.ready')
                except Exception:                       # noqa: BLE001
                    log.warning('font readiness check failed; PDF may use a fallback face')
                # Footer on EVERY page, from the print engine — Chromium cannot
                # repeat an in-flow element, and the in-flow one printed once,
                # under the signatures, claiming "Page 1 of 1" (CFO 2026-08-10).
                # These margins are the ONE source of truth for the page box: the
                # template must not carry an @page margin, or it overrides these
                # and content lays out over the full page height, straight under
                # this footer (the overlap the CFO caught on 11 Aug 2026). The
                # letterhead is white now, so a real top margin costs nothing.
                ref = escape(quote.quote_number or '')
                foot = (
                    '<div style="width:100%;font-family:Arial,sans-serif;font-size:8px;'
                    'color:#6B7280;padding:0 44px 6px;display:flex;justify-content:space-between">'
                    '<span>Alpha Direct Insurance Company (Pty) Ltd &middot; Licensed by NBFIRA</span>'
                    f'<span>{ref} &middot; Page <span class="pageNumber"></span>'
                    ' of <span class="totalPages"></span></span></div>')
                return page.pdf(format='A4', print_background=True,
                                display_header_footer=True,
                                header_template='<div></div>', footer_template=foot,
                                margin={'top': '8mm', 'bottom': '15mm',
                                        'left': '0', 'right': '0'})
            finally:
                browser.close()
    finally:
        _SEM.release()
