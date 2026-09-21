"""underwriting/quote_doc_gate.py — the document checks itself before it goes out.

Built after 11 Aug 2026, when a quotation reached the CFO twice with print
faults a reader spots in seconds: schedule rows printed through the page
footer, a table cut across two pages, the totals row invisible. Every fault
appeared only on the PRINTED page — the on-screen render looked right — and
every fault appeared because the document's SIZE changed (a 15-vehicle fleet)
and nobody re-measured the print.

So the measuring is now done by the machine, on every issue, in two layers:

MEASURE (deterministic, always on)
    The rendered PDF is reopened and every piece of text on every page is
    measured — pdfium gives each text run's rectangle in points. Two checks,
    each one a fault that actually shipped and that geometry can HONESTLY catch:
      * a real content line inside the footer band — the "rows printed through
        the licence line" fault (pages 3-4 of the 11 Aug print);
      * a near-blank page — the stranded-block fault (payment options alone on
        a last page).
    No AI, no network, no judgement: the text is either in the band or it is not.

    Deliberately NOT done here: "text on top of text". The two overlap faults
    that shipped were a stamp IMAGE over text and a totals row printed navy-on-
    navy (a colour fault, the text was not moved) — neither is two text runs in
    the same place, so a text-overlap check would be theatre. Those belong to
    the review layer, which reads the words, and to the fixed-and-tested cases.

DUPLICATE (deterministic, always on)
    The same cover line entered twice — same name, same sum insured, same
    premium — is a double-charge. Read from the stored rows, not judged, so it
    also HARD-BLOCKS. Two genuinely-separate units (labelled, different serial)
    are not identical and pass.

REVIEW (Aria, ADVISORY only — never a hard block)
    The page TEXT goes to Aria (core.ai_assist.deepseek_complete — PII firewall,
    key from the vault, backup engine, the path the quote parser already uses)
    with one question: would a careful underwriting manager send this? It reads
    for what geometry cannot — a heading repeated, totals that do not reconcile,
    mixed spellings. But it also MISREADS: on a personal-lines quote it took the
    "Family liability P2,000,000" sub-line for a building's sum insured and
    failed the document (12 Aug 2026). An AI that can hallucinate must not VETO a
    correct quote, or it blocks every good one. So its findings are surfaced as
    WARNINGS for a human to weigh; only the deterministic layers decide go/no-go.
    If Aria is unreachable, nothing is lost — the blocking layers stand alone.

The gate runs at ISSUE — the moment a draft becomes a client document. The
issue endpoint wraps the status change and the BLOCKING checks in one
transaction, so a blocked document rolls the whole issue back and the quote
stays a draft with the reasons on screen; Aria's advisory notes ride along on
the response without stopping the issue.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

# The PDF engine reserves the bottom 15mm (42.5pt) for the per-page footer
# (quote_render.render_quote_pdf margins). The footer's OWN words live at the
# very bottom (~19-25pt); a schedule row that has spilled DOWN into the reserved
# strip is the fault. So the trip line sits a little above the footer's own
# baseline, and the footer's page-number fragments ("Page", "of", "3") and the
# licence line are exempted explicitly — they are meant to be there.
_FOOTER_BAND_PT = 34.0
_FOOTER_WORDS = ('alpha direct', 'nbfira', 'page', 'q-2026', 'quotation')
# A real content line is long. The footer's own markers are a word or a number;
# exempting anything short keeps "of", "3", "Page 5 of 6" from reading as an
# intrusion while a spilled schedule row ("B290BUN Nissan Truck 1,552,000.00")
# is well over the limit.
_MIN_INTRUSION_CHARS = 12
_MIN_AREA_PT2 = 8.0          # ignore dots, rules and hairline fragments


def _rect_area(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _is_footer_text(text: str) -> bool:
    t = ' '.join((text or '').lower().split())
    if len(t) < _MIN_INTRUSION_CHARS:
        return True                                 # a page marker, not content
    return any(w in t for w in _FOOTER_WORDS)


def geometry_findings(pdf_bytes: bytes) -> list[str]:
    """Measure the printed pages. Empty list = the geometry is clean.

    Returns plain-English findings, each naming its page, so the person who
    hits the block knows exactly what to look at. Raises nothing: if the PDF
    cannot be measured the finding says so — an unmeasurable document should
    be looked at by a human, not waved through.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        # The measuring library is missing from the image. Do not block every
        # issue on a packaging problem — log loudly and let the review layer
        # (and the humans) carry the check.
        log.error('pypdfium2 missing — quotation geometry checks are OFF')
        return []

    findings: list[str] = []
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:                               # noqa: BLE001
        return ['The rendered PDF could not be opened for checking.']

    try:
        for pno in range(len(pdf)):
            page = pdf[pno]
            tp = page.get_textpage()
            n = tp.count_rects()
            rects = []
            for i in range(n):
                r = tp.get_rect(i)
                if _rect_area(r) < _MIN_AREA_PT2:
                    continue
                text = (tp.get_text_bounded(*r) or '').strip()
                if text:
                    rects.append((r, text))

            # A page with almost nothing on it is a pagination fault — a block
            # got stranded. The last page legitimately runs short, so only
            # flag emptiness before it.
            if len(rects) < 5 and pno < len(pdf) - 1:
                findings.append(f'Page {pno + 1} is nearly blank — a block has been stranded.')

            # A real content line must stay out of the reserved footer strip.
            for r, text in rects:
                if r[1] < _FOOTER_BAND_PT and not _is_footer_text(text):
                    findings.append(
                        f'Page {pno + 1}: "{text[:60]}" prints inside the footer band.')
                    break                           # one per page says enough
    finally:
        pdf.close()
    return findings


def page_texts(pdf_bytes: bytes, per_page_chars: int = 2400) -> list[str]:
    """The document's text, page by page, for the review layer."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception:                               # noqa: BLE001
        return []
    try:
        return [pdf[i].get_textpage().get_text_bounded()[:per_page_chars]
                for i in range(len(pdf))]
    finally:
        pdf.close()


_REVIEW_SYSTEM = """You are the final reviewer of an insurance quotation before it is \
issued to a client. You are given the text of each printed page. Judge whether a \
careful underwriting manager would send it.

Look ONLY for document faults:
- rows that are just dashes with no figures and no purpose
- the same section heading or the same item appearing twice
- an excess or condition repeated line after line
- a totals figure that does not equal the sum of its rows
- mixed spellings or casing of the same word
- headings separated from their content, or blocks that read as stranded

Do NOT judge the commercial terms, the premium level, or the cover itself.

Return ONLY a JSON object: {"verdict": "PASS" or "FAIL", "issues": ["...", ...]}.
FAIL only for faults a client would notice; list each one plainly."""


def aria_review(texts: list[str]) -> list[str]:
    """Aria's read of the document. Empty list = PASS or Aria unavailable.

    Fail-open by design: omni must keep working when the AI is down, and the
    geometry layer has already run. A FAIL verdict returns its issues, which
    block the issue with reasons the underwriter can act on.
    """
    if not texts:
        return []
    try:
        from core.ai_assist import deepseek_complete, DeepSeekUnavailable
    except Exception:                               # noqa: BLE001
        return []
    body = '\n\n'.join(f'--- page {i + 1} ---\n{t}' for i, t in enumerate(texts))
    try:
        raw = deepseek_complete(body, system_prompt=_REVIEW_SYSTEM,
                                response_format='json_object', max_tokens=600)
        parsed = json.loads(raw)
    except (DeepSeekUnavailable, ValueError, json.JSONDecodeError):
        return []
    except Exception:                               # noqa: BLE001 — never block on the reviewer crashing
        log.exception('quotation review layer failed; geometry checks stand alone')
        return []
    if not isinstance(parsed, dict) or str(parsed.get('verdict', '')).upper() != 'FAIL':
        return []
    issues = [str(x).strip() for x in (parsed.get('issues') or []) if str(x).strip()]
    # A FAIL with no stated reason is not actionable — treat it as a pass
    # rather than block an underwriter with "the AI said no".
    return issues[:6]


def duplicate_findings(sections) -> list[str]:
    """Deterministic: the SAME cover line entered twice — same name, same sum
    insured, same premium. Reliable, so it HARD-BLOCKS: it is exactly the
    double-charge the gate exists to stop, and unlike the AI read it cannot be a
    hallucination. Two genuinely-different units (labelled unit 1 / unit 2, a
    different serial) are not identical and do not trip it.
    """
    seen: dict[tuple, int] = {}
    dupes: list[str] = []
    for s in (sections or []):
        if not isinstance(s, dict):
            continue
        name = ' '.join(str(s.get('name') or '').split()).casefold()
        if not name:
            continue
        key = (name, str(s.get('sum_insured') or '').strip(),
               str(s.get('rate') or '').strip(), str(s.get('premium') or '').strip())
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            dupes.append(
                f'The cover line "{str(s.get("name") or "").strip()}" appears more '
                'than once with the same sum insured and premium — it may be '
                'charged twice. Remove the duplicate, or label the units if they '
                'are genuinely separate.')
    return dupes


def blocking_findings(pdf_bytes: bytes, sections=None) -> list[str]:
    """The DETERMINISTIC faults — geometry plus exact-duplicate lines. These are
    reliable (measured, not judged), so they hard-block the issue."""
    problems = geometry_findings(pdf_bytes)
    if sections is not None:
        problems += duplicate_findings(sections)
    return problems


def advisory_findings(pdf_bytes: bytes) -> list[str]:
    """Aria's read — for a HUMAN to weigh, never a hard block. The AI catches
    what geometry cannot (a heading repeated, totals that do not reconcile) but
    it also misreads a sub-line as a figure, so it must not veto a document on
    its own. Surfaced as warnings; the deterministic layer decides go/no-go.
    """
    return aria_review(page_texts(pdf_bytes))


def gate_quote_pdf(pdf_bytes: bytes, sections=None) -> list[str]:
    """Back-compat: the BLOCKING findings only. Callers that also want Aria's
    advisory notes call advisory_findings() separately and surface them without
    blocking (the issue endpoint does exactly this)."""
    return blocking_findings(pdf_bytes, sections)
