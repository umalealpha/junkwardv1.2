"""
procurement/claims_parser.py — vehicle-assessment PDF parser (PHASE 3 of the port).

Ported OUT of the dead `po-invoice-ingest` FastAPI app (assessment_parser.py)
INTO omni, per CFO 2026-07-06. No Odoo, no HTTP, no FX, no DeepSeek — this is
the deterministic parser only, and it is the source of truth for numbers.

These are NOT plain invoices. Structure (verified against
"Assessment report.pdf", claim G2026004607):

  • Header: Claim no, Assessment no, Work Provider, Repairer, Vehicle.
  • Parts table: every row carries a `Supplier` column → parts must be
    grouped per supplier into separate POs.
  • Labour table: a single rate × units; the labour PO goes to the Repairer.
  • Summary: Parts / Labour / Paint / Sundries / Excess / Grand Total (excl) /
    Total (incl) — the incl figure carries 14% VAT.

All money in the report is PRE-VAT.

Output: `parse(pdf_path)` returns the plain `report_dict` that
procurement/claims_engine.py (Phase 1) consumes:

  summary   dict  — {"Paint": float, "Sundries": float, "Excess": float, ...}
  groups    list  — [{kind: "parts"|"labour", supplier_label, subtotal_excl,
                      lines: [{code?, description, qty, unit_price, markup?,
                               units?, rate?, total?}]}]
  top-level str?  — repairer, claim_no, policy_no, assessment_no,
                    client_name, vehicle, vehicle_reg, client_contact

Layering (so the parsing is unit-testable without a real PDF):

  _extract(pdf_path)  — pdfplumber only: text + raw table rows.
  parse_extracted(..) — deterministic rows→structure logic (pure, testable).
  parse(pdf_path)     — the two glued together, returns a plain dict.

One adaptation vs the source: parts lines gain a derived `markup` key
(total − qty × unit_price) because claims_engine's claims_split() sums
per-line markup while pricing parts at BASE (qty × unit_price); the retired
app derived the same figure downstream.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

try:  # import-guarded per omni convention; pdfplumber>=0.11.0 is in requirements.txt
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None

VAT_RATE = 0.14


@dataclass
class PartLine:
    code: str
    description: str
    supplier: str          # raw supplier label from the report
    qty: float
    unit_price: float      # pre-VAT
    total: float           # pre-VAT (qty * unit_price + markup)
    markup: float = 0.0    # derived: total - qty*unit_price (engine needs it per line)


@dataclass
class LabourLine:
    code: str
    description: str
    units: float
    rate: float
    total: float           # pre-VAT


@dataclass
class SupplierGroup:
    supplier_label: str
    kind: str              # "parts" | "labour"
    lines: list[dict] = field(default_factory=list)

    @property
    def subtotal_excl(self) -> float:
        return round(sum(l["total"] for l in self.lines), 2)

    @property
    def vat(self) -> float:
        return round(self.subtotal_excl * VAT_RATE, 2)

    @property
    def total_incl(self) -> float:
        return round(self.subtotal_excl + self.vat, 2)


@dataclass
class AssessmentReport:
    claim_no: str | None = None
    policy_no: str | None = None
    assessment_no: str | None = None
    work_provider: str | None = None
    repairer: str | None = None
    client_name: str | None = None      # Insured
    client_contact: str | None = None   # Cell / phone
    vehicle: str | None = None          # make/model line, when printed
    vehicle_reg: str | None = None
    currency: str = "BWP"
    groups: list[SupplierGroup] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    raw_text: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["groups"] = [
            {
                "supplier_label": g.supplier_label,
                "kind": g.kind,
                "lines": g.lines,
                "subtotal_excl": g.subtotal_excl,
                "vat": g.vat,
                "total_incl": g.total_incl,
            }
            for g in self.groups
        ]
        return d


_MONEY = re.compile(r"P?\s*\(?\s*([\d,]+\.\d{2})\s*\)?")


def _money(s: str | None) -> float:
    if not s:
        return 0.0
    m = _MONEY.search(s)
    if not m:
        return 0.0
    return float(m.group(1).replace(",", ""))


def is_assessment_report(text: str) -> bool:
    """Heuristic: does this PDF look like an Alpha assessment report?"""
    t = text.lower()
    return ("assessment" in t and "claim" in t and "parts" in t
            and ("supplier" in t or "repairer" in t))


def _search(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _norm_supplier(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().upper()


# ---------------------------------------------------------------------------
# PDF extraction (the ONLY part that touches pdfplumber)
# ---------------------------------------------------------------------------

def _extract(pdf_path: str) -> tuple[str, list[list], list[list], list[list]]:
    """Pull raw text + classified table rows out of the PDF. Table headers:
    Supplier+Code → parts; Unit(s)+Code → labour; Description|Total → summary."""
    if pdfplumber is None:  # pragma: no cover
        raise RuntimeError(
            "pdfplumber is not installed — claims assessment parsing needs it "
            "(it is pinned in requirements.txt; pip install pdfplumber)"
        )
    all_text: list[str] = []
    parts_rows: list[list] = []
    labour_rows: list[list] = []
    summary_rows: list[list] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            all_text.append(txt)
            for tbl in page.extract_tables():
                if not tbl:
                    continue
                header = [(c or "").strip().lower() for c in tbl[0]]
                if "supplier" in header and "code" in header:
                    parts_rows.extend(tbl[1:])
                elif "unit(s)" in header and "code" in header:
                    labour_rows.extend(tbl[1:])
                elif header[:2] == ["description", "total"]:
                    summary_rows.extend(tbl[1:])
    return "\n".join(all_text), parts_rows, labour_rows, summary_rows


# ---------------------------------------------------------------------------
# Deterministic rows→structure parsing (pure — unit-testable without a PDF)
# ---------------------------------------------------------------------------

def parse_extracted(
    full: str,
    parts_rows: list[list],
    labour_rows: list[list],
    summary_rows: list[list],
) -> AssessmentReport:
    rep = AssessmentReport()
    rep.raw_text = full

    # ── header meta ──────────────────────────────────────────────
    rep.claim_no = _search(r"Claim:\s*(\S+)", full)
    # Policy number (Kao 2026-06-22: essential on every PO, was never parsed).
    # Local regex only — policy is PII and must NEVER reach an external LLM.
    # Require a digit so prose like "Policy holder" can't false-match.
    _pol = _search(r"(?i)\bPolicy(?:\s+(?:no\.?|number|#))?\s*[:#\-]?\s*([A-Za-z0-9/\-]{6,})", full)
    if _pol and any(c.isdigit() for c in _pol):
        rep.policy_no = _pol
    rep.assessment_no = _search(r"Assessment:\s*(\S+)", full)
    # The detail block prints "Name: <Work Provider> Name: <Repairer>"
    # on one line. The repairer is the SECOND Name on that line.
    rep.work_provider = _search(r"Name:\s*(.+?)\s+Name:", full)
    rep.repairer = _search(r"Name:\s*.+?\s+Name:\s*(.+?)\s*$", full)
    # Fallback: many assessments print the repairer on its own header line,
    # e.g. "Repairer: Rolling Wheels (Gabs)" (claim B915BEL, 2026-06-19).
    # Strip any trailing branch suffix in parentheses so it merges with the
    # same vendor's parts group instead of raising a second PO.
    if not rep.repairer:
        m = re.search(r"Repairer\s*:\s*(.+)", full, re.IGNORECASE)
        if m:
            rep.repairer = re.sub(r"\s*\([^)]*\)\s*$", "", m.group(1)).strip() or None
    rep.vehicle = _search(r"Vehicle:\s*(.+?)\s*$", full)
    rep.vehicle_reg = _search(r"Registration:\s*(\S+)", full)
    # Insured (client) = second "Name:" on the line under "Assessor Insured".
    rep.client_name = _search(r"Assessor\s+Insured\s*\nName:\s*.+?\s+Name:\s*(.+?)\s*$", full)
    # Contact: prefer the Insured's Cell, else any phone in the block.
    rep.client_contact = _search(r"Cell:\s*(\S+)", full)

    # ── parts → group by supplier ────────────────────────────────
    groups: dict[str, SupplierGroup] = {}
    for row in parts_rows:
        if not row or len(row) < 9:
            continue
        code = (row[0] or "").strip()
        desc = (row[1] or "").replace("\n", " ").strip()
        supplier = (row[2] or "").replace("\n", " ").strip()
        if not supplier or supplier.lower() == "supplier":
            continue
        # skip the trailing totals row (no code/supplier)
        if not code and not supplier:
            continue
        qty = _money(row[4]) or 1.0
        unit_price = _money(row[5])
        total = _money(row[8])
        if total == 0 and unit_price == 0:
            continue
        # derived per-line markup for the engine (total = qty*unit + markup)
        markup = round(total - qty * unit_price, 2)
        if markup < 0:
            markup = 0.0
        sup_key = _norm_supplier(supplier)
        g = groups.setdefault(sup_key, SupplierGroup(supplier_label=supplier, kind="parts"))
        g.lines.append(PartLine(code, desc, supplier, qty, unit_price, total, markup).__dict__)

    # ── labour → repairer ────────────────────────────────────────
    labour_lines: list[dict] = []
    for row in labour_rows:
        if not row or len(row) < 5:
            continue
        code = (row[0] or "").strip()
        desc = (row[1] or "").replace("\n", " ").strip()
        # Skip the table footer total row (no code / no description).
        if not code or not desc:
            continue
        units = _money(row[2])
        rate = _money(row[3])
        total = _money(row[4])
        if total <= 0:
            continue
        labour_lines.append(LabourLine(code, desc, units, rate, total).__dict__)

    rep.groups = list(groups.values())
    if labour_lines:
        # Pick the vendor that the labour PO belongs to:
        #   1. Use the explicit Repairer on the report if the parser found one.
        #   2. Else, if a parts supplier name looks like a repairer (panel
        #      beaters / autobody / workshop / repair), attribute labour to
        #      THAT supplier so we don't raise two POs to the same vendor
        #      (CFO directive 2026-06-09, Kao feedback).
        #   3. Else, if there is exactly one parts supplier, use it.
        #   4. Last resort: generic placeholder the user can re-assign.
        repairer_label = rep.repairer
        if not repairer_label:
            # Genuine panel-beater / repairer indicators ONLY. Do NOT include
            # "motor centre" / "motors" — those are typically parts dealers
            # (e.g. Motor Centre Toyota), and matching them mis-attributed the
            # repairer's labour/paint to the parts supplier (claim B915BEL).
            hint = re.compile(
                r"(panel\s*beater|panel\s*beaters|panelbeaters|autobody|auto\s*body|"
                r"workshop|repairs?\b|spray\s*paint|smash|body\s*works?)",
                re.IGNORECASE,
            )
            cand = next((g.supplier_label for g in rep.groups if hint.search(g.supplier_label)), None)
            if cand is None and len(rep.groups) == 1:
                cand = rep.groups[0].supplier_label
            repairer_label = cand or "Repairer (Labour)"
        rep.groups.append(
            SupplierGroup(
                supplier_label=repairer_label,
                kind="labour",
                lines=labour_lines,
            )
        )

    # ── summary ──────────────────────────────────────────────────
    for row in summary_rows:
        if not row or len(row) < 2 or not row[0]:
            continue
        rep.summary[row[0].strip().rstrip(":")] = _money(row[1])

    return rep


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse(pdf_path: str) -> dict:
    """Parse an assessment PDF into the plain report_dict claims_engine reads."""
    full, parts_rows, labour_rows, summary_rows = _extract(pdf_path)
    return parse_extracted(full, parts_rows, labour_rows, summary_rows).to_dict()
