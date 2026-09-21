"""
procurement/claims_engine.py — Claims PO money engine (PHASE 1 of the port).

Ported OUT of the dead `po-invoice-ingest` FastAPI app (Odoo) INTO omni,
per CFO 2026-07-06. This module is the PURE arithmetic only — the excess /
VAT / markup rules and the 2-PO split (Repairer vs Parts). It has NO Odoo,
NO HTTP, NO FX conversion (exchange-rate handling dropped per CFO "ignore
the 1.15 / 1.33 rate"), and touches no database, so it is unit-testable in
isolation. Vendor resolution + PO creation + assessment parsing land in
later phases (they need omni's billing.Contact / PurchaseOrder).

Rules preserved verbatim from Lemogang (Parts) + Kao (Claims) email rulings
(see test_claims_engine.py for the reproduced examples):

  * 2-PO split: Repairer PO = its own parts + labour + paint + markup +
    sundries − excess; Parts PO(s) = OUTSIDE-supplier parts at BASE price
    (markup NOT folded into the supplier line).
    (Kao 2026-06-29: "Parts ordered by the supplier come on a different PO
    separate from the PO with Markup, Labour, Excess".)
  * Excess amount priority: ① a directly-typed amount wins (dynamic) →
    ② the assessment's own Excess figure → ③ max(pct × repairer base, min).
    (Kao 2026-06-29: "type the exact amount … wins over assessment and %/min".)
  * Minimum-excess floor: if the excess is below the minimum, lift it to the
    minimum — but only when a minimum was explicitly entered for the claim, so
    claims with no matrix minimum keep the assessment's own excess (no
    regression). (Lemogang 2026-06-19 #1; Kao 2026-06-22.)
  * NO VAT on the excess: it is the client's own contribution, shown as a flat
    negative line with no tax; VAT applies to the repair work only.
    (Lemogang 2026-06-19 #2; Kao 2026-06-22.)
  * Markup: a per-claim % of the total parts value, earned ONLY on
    supplier-sourced parts — never on parts the repairer sourced itself.
    (Lemogang 2026-06-19 #5, tightened 2026-06-22.)

Defaults: VAT 14%, excess 5%, minimum P5,000.
"""
from __future__ import annotations

import re

VAT_RATE = 0.14
DEFAULT_EXCESS_PCT = 0.05      # 5%
DEFAULT_EXCESS_MIN = 5000.0    # P5,000 floor


# ---------------------------------------------------------------------------
# Name normalisation + line builders (pure)
# ---------------------------------------------------------------------------

def _name_norm(s: str) -> str:
    """Normalise a vendor name for substring matching: lowercase, strip
    punctuation and common corporate suffixes so "Carfil Services (Pty)Ltd"
    matches "Carfil Services (PTY) LTD"."""
    s = (s or "").lower()
    s = re.sub(r"[().,\-/&]", " ", s)
    s = re.sub(r"\b(pty|ltd|inc|co|corp|limited|company|the)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Narrow repairer-name detector. Deliberately excludes the generic word
# "motors" and "motor centre" (those matched parts dealers and sent labour to
# the wrong vendor — invoice #17 / claim B915BEL). Keep specific indicators.
_REPAIRER_NAME_RE = re.compile(
    r"(panel\s*beater|panelbeaters|panel\s*beat|autobody|auto\s*body|"
    r"panel\s*&?\s*paint|workshop\b|repair|spray\s*paint|smash|body\s*works?)",
    re.IGNORECASE,
)


def _part_line(ln: dict) -> dict:
    """Odoo-free parts line at BASE price (qty * unit_price). The repairer's
    markup on bought-in parts is NOT folded in here — it is a separate Markup
    line on the repairer PO (see build_allocation_plan)."""
    desc = f"[{ln['code']}] {ln['description']}" if ln.get("code") else ln["description"]
    qty = float(ln.get("qty") or 1.0)
    unit = float(ln.get("unit_price") or 0.0)
    return {"description": desc, "qty": qty, "unit_price": unit}


def _labour_line(ln: dict) -> dict:
    """Labour line. Assessments carry lump-sum rows with units=0 but a real
    Total (e.g. 'POLISH & GLAZE' units 0, Total 1,600). units*rate is only
    trustworthy when it reproduces Total; otherwise bill the Total as one line
    (found on claim G2026004786 — P5,380 of labour was being dropped)."""
    units = float(ln.get("units") or 0.0)
    rate = float(ln.get("rate") or 0.0)
    total = float(ln.get("total") or 0.0)
    if units > 0 and rate > 0 and abs(units * rate - total) <= 0.05:
        return {"description": ln["description"], "qty": units, "unit_price": rate}
    if total > 0:
        return {"description": ln["description"], "qty": 1.0, "unit_price": total}
    return {"description": ln["description"], "qty": units or 1.0, "unit_price": rate}


# ---------------------------------------------------------------------------
# Per-supplier VAT/currency default (SA suppliers). Currency is recorded for a
# later phase; NO FX conversion happens here (dropped per CFO).
# ---------------------------------------------------------------------------

_SA_DEFAULT_TOKENS = ("cfao", "smg", "autoworld", "ace auto")


def sa_supplier_default(label: str) -> dict:
    """SA suppliers (CFAO, SMG, Omega Autoworld, Ace Auto) -> VAT excluded +
    ZAR; everyone else -> VAT + BWP. Conservative match so a plain 'OMEGA'
    (a BWP parts dealer) is NOT auto-flipped to ZAR."""
    norm = _name_norm(label)
    is_sa = any(tok in norm for tok in _SA_DEFAULT_TOKENS)
    return {"vat": not is_sa, "currency": "ZAR" if is_sa else "BWP"}


def _resolve_supplier_setting(labels, supplier_settings, default_vat, default_ccy):
    """Pick the per-supplier VAT/currency for a PO bucket; fall back to the
    global defaults when none is set."""
    for lbl in labels:
        s = (supplier_settings or {}).get(lbl)
        if s:
            vat = bool(s["vat"]) if "vat" in s else default_vat
            return vat, (s.get("currency") or default_ccy)
    return default_vat, default_ccy


# ---------------------------------------------------------------------------
# 2-PO split summary (excl / VAT / incl)
# ---------------------------------------------------------------------------

def claims_split(
    report_dict: dict,
    excess_pct: float | None = None,
    excess_min: float | None = None,
    markup_pct: float | None = None,
    excess_amount: float | None = None,
) -> dict:
    """Claims 2-PO model. Repairer (specialised) PO = the repairer's OWN
    parts + labour + paint + markup + sundries LESS excess; Parts
    (motor-centre) PO = outside-supplier parts at base only.

    Parts groups whose supplier matches the repairer (B915BEL: "ROLLING
    WHEELS" parts under repairer "Rolling Wheels") are billed on the REPAIRER
    PO — that is how build_allocation_plan + create-pos bucket them (by
    vendor), so the preview must say the same.

    All figures pre-VAT; 14% VAT applied on the repair work only. Excess is a
    flat, NO-VAT deduction. Returns both PO blocks with excl / VAT / incl.
    """
    pct = DEFAULT_EXCESS_PCT if excess_pct is None else float(excess_pct)
    minimum = DEFAULT_EXCESS_MIN if excess_min is None else float(excess_min)

    summary = report_dict.get("summary") or {}
    groups = report_dict.get("groups") or []

    # Repairer resolution mirrors build_allocation_plan so both agree on which
    # parts groups are the repairer's own (NO markup on those — rule 4).
    rep = (report_dict.get("repairer") or "").strip()
    if not rep:
        for g in groups:
            if g["kind"] == "labour":
                rep = g.get("supplier_label") or ""
                break
    rep_norm = _name_norm(rep)

    # Parts at BASE price (qty × unit). The markup embedded in a line's total
    # is the repairer's separate Markup line — it must NOT also sit in the
    # motor-centre block (that double-counted it in the grand total).
    # The repairer's OWN parts belong on the REPAIRER block (no markup on
    # them — rule 4); only outside-supplier parts make the Parts PO block.
    repairer_parts_base = 0.0     # repairer-sourced parts — billed on ITS PO
    supplier_parts_base = 0.0     # outside-supplier parts base — the Parts PO
    supplier_parts_excl = 0.0     # non-repairer parts only — the markup base
    supplier_parts_markup = 0.0   # embedded markup on those parts
    for g in groups:
        if g["kind"] != "parts":
            continue
        base_amt = round(sum(
            float(l.get("qty") or 1.0) * float(l.get("unit_price") or 0.0)
            for l in g.get("lines", [])
        ), 2)
        sup_norm = _name_norm(g.get("supplier_label") or "")
        is_repairer_parts = bool(rep_norm) and (
            sup_norm == rep_norm or rep_norm in sup_norm or sup_norm in rep_norm
        )
        if is_repairer_parts:
            repairer_parts_base += base_amt
        else:
            supplier_parts_base += base_amt
            supplier_parts_excl += float(g.get("subtotal_excl") or 0)
            supplier_parts_markup += float(g.get("subtotal_excl") or 0) - base_amt
    repairer_parts_base = round(repairer_parts_base, 2)
    supplier_parts_base = round(supplier_parts_base, 2)
    supplier_parts_excl = round(supplier_parts_excl, 2)

    labour_total = round(sum(g["subtotal_excl"] for g in groups if g["kind"] == "labour"), 2)
    paint = float(summary.get("Paint", 0) or 0)
    sundries = float(summary.get("Sundries", 0) or 0)
    anti_corrosion = float(summary.get("Anti-Corrosion", 0) or 0)
    underside_paint = float(summary.get("Underside Paint", 0) or 0)
    if markup_pct is not None:
        # markup is a per-claim % of the SUPPLIER-sourced parts value only
        # (rule 4 — same base as build_allocation_plan).
        markup = round(supplier_parts_excl * float(markup_pct) / 100.0, 2)
    else:
        markup = round(float(summary.get("Markup") or 0) or supplier_parts_markup, 2)
    if markup < 0:
        markup = 0.0   # build_allocation_plan drops a non-positive markup row

    # Everything build_allocation_plan routes to the repairer — incl. the
    # repairer's OWN parts and the Anti-Corrosion / Underside Paint extras —
    # so this block matches the repairer PO that create-pos actually raises.
    specialised_gross = round(
        repairer_parts_base + labour_total + paint + markup + sundries
        + anti_corrosion + underside_paint, 2
    )
    excess = float(summary.get("Excess") or 0)
    if excess <= 0 and specialised_gross:
        excess = round(max(specialised_gross * pct, minimum), 2)
    elif excess > 0 and excess_min is not None:
        # matrix minimum — only when explicitly entered (no regression).
        excess = round(max(excess, float(excess_min)), 2)
    # A directly-typed excess amount is dynamic and wins over everything above.
    if excess_amount is not None and float(excess_amount) > 0:
        excess = round(float(excess_amount), 2)

    def vat_block(excl: float) -> dict:
        excl = round(excl, 2)
        vat = round(excl * VAT_RATE, 2)
        return {"excl": excl, "vat": vat, "incl": round(excl + vat, 2)}

    # Excess is a flat, NO-VAT deduction: VAT applies to the repair work only;
    # the excess comes off the incl total as-is.
    spec_vat = round(specialised_gross * VAT_RATE, 2)
    specialised_block = {
        "repairer_parts": repairer_parts_base,   # repairer's own parts, base
        "labour": labour_total,
        "paint": paint,
        "markup": markup,
        "sundries": sundries,
        "anti_corrosion": anti_corrosion,
        "underside_paint": underside_paint,
        "gross_excl": specialised_gross,
        "excess": excess,           # negative PO line — client pays the repairer
        "excess_pct": pct,
        "excess_min": minimum,
        "excl": round(specialised_gross - excess, 2),
        "vat": spec_vat,
        "incl": round(specialised_gross + spec_vat - excess, 2),
    }
    motor_block = {"parts_total": supplier_parts_base, **vat_block(supplier_parts_base)}

    return {
        "specialised": specialised_block,
        "motor_centre": motor_block,
        "grand": {
            "excl": round(specialised_block["excl"] + motor_block["excl"], 2),
            "vat": round(specialised_block["vat"] + motor_block["vat"], 2),
            "incl": round(specialised_block["incl"] + motor_block["incl"], 2),
        },
    }


# ---------------------------------------------------------------------------
# Allocation plan (row-per-line "who gets paid for what")
# ---------------------------------------------------------------------------

def build_allocation_plan(report_dict: dict, markup_pct: float | None = None) -> dict:
    """Turn an assessment report into a row-per-line allocation plan. Parts
    rows are per supplier at BASE price; labour is one consolidated row;
    markup + paint + sundries land on the repairer. Uses the vendors named on
    the report (not hardcoded 'Specialised'/'Motor Centre' — Kao 2026-06-29)."""
    summary = report_dict.get("summary") or {}
    groups = report_dict.get("groups") or []

    rep = (report_dict.get("repairer") or "").strip()
    if not rep:
        for g in groups:
            if g["kind"] == "labour":
                rep = g.get("supplier_label") or ""
                break
    if not rep:
        rep = "(pick repairer)"

    rows: list[dict] = []
    parts_vendor_labels: list[str] = []
    parts_markup_total = 0.0
    supplier_parts_total_excl = 0.0   # non-repairer parts only — the markup base
    rep_norm = _name_norm(rep)

    for g in groups:
        if g["kind"] != "parts":
            continue
        sup = g["supplier_label"]
        parts_vendor_labels.append(sup)
        lines = [_part_line(ln) for ln in g.get("lines", [])]
        base_amt = round(sum(float(l["qty"]) * float(l["unit_price"]) for l in lines), 2)
        # markup earned ONLY on parts supplied by outside suppliers — NEVER on
        # parts the repairer sourced itself. Exclude the repairer's own group.
        sup_norm = _name_norm(sup)
        is_repairer_parts = bool(rep_norm) and (
            sup_norm == rep_norm or rep_norm in sup_norm or sup_norm in rep_norm
        )
        if not is_repairer_parts:
            parts_markup_total += float(g.get("subtotal_excl") or 0) - base_amt
            supplier_parts_total_excl += float(g.get("subtotal_excl") or 0)
        rows.append({
            "id": f"parts:{sup}",
            "category": "Parts",
            "label": f"{sup} ({len(lines)} item{'s' if len(lines) != 1 else ''})",
            "amount": base_amt,
            "default_vendor": sup,
            "lines": lines,
        })
    parts_markup_total = round(parts_markup_total, 2)
    supplier_parts_total_excl = round(supplier_parts_total_excl, 2)

    # labour is NOT itemised — one consolidated line. Sum ALL labour groups
    # (claims_split sums them too); billing only the FIRST group made the
    # plan/PO disagree with the split preview on a multi-labour-group report
    # — the PO silently dropped the later groups' money.
    labour_groups = [g for g in groups if g["kind"] == "labour"]
    labour_total = round(
        sum(float(g.get("subtotal_excl") or 0) for g in labour_groups), 2)
    labour_lines: list[dict] = (
        [{"description": "Labour", "qty": 1.0, "unit_price": labour_total}]
        if labour_groups else []
    )
    if labour_total > 0 or labour_lines:
        rows.append({
            "id": "labour",
            "category": "Labour",
            "label": "Labour",
            "amount": labour_total,
            "default_vendor": rep,
            "lines": labour_lines or [{"description": "Labour", "qty": 1, "unit_price": labour_total}],
        })

    if markup_pct is not None:
        markup_amt = round(supplier_parts_total_excl * float(markup_pct) / 100.0, 2)
    else:
        markup_amt = round(float(summary.get("Markup") or 0) or parts_markup_total, 2)
    if markup_amt > 0:
        rows.append({
            "id": "markup",
            "category": "Markup",
            "label": "Markup",
            "amount": markup_amt,
            "default_vendor": rep,
            "lines": [{"description": "Markup", "qty": 1, "unit_price": markup_amt}],
        })

    extras = (
        ("paint", "Paint", float(summary.get("Paint") or 0)),
        ("sundries", "Sundries", float(summary.get("Sundries") or 0)),
        ("anti_corrosion", "Anti-Corrosion", float(summary.get("Anti-Corrosion") or 0)),
        ("underside_paint", "Underside Paint", float(summary.get("Underside Paint") or 0)),
    )
    for rid, cat, amt in extras:
        if amt <= 0:
            continue
        rows.append({
            "id": rid,
            "category": cat,
            "label": cat,
            "amount": amt,
            "default_vendor": rep,
            "lines": [{"description": cat, "qty": 1, "unit_price": amt}],
        })

    options: list[str] = []
    seen: set[str] = set()
    for lbl in parts_vendor_labels + [rep]:
        if lbl and lbl not in seen:
            options.append(lbl)
            seen.add(lbl)

    return {"rows": rows, "vendor_options": options, "repairer": rep}


def apply_allocations(plan: dict, saved: list[dict] | None) -> list[dict]:
    """Merge user vendor overrides into the default plan.

    Saved entries are ``{"id", "vendor"}`` label overrides; an entry may also
    carry an optional ``"vendor_id"`` (a billing.Contact PK picked on the
    review screen) which is carried through so create-pos can resolve the row
    directly, bypassing name matching."""
    overrides: dict[str, dict] = {}
    for s in saved or []:
        if isinstance(s, dict) and s.get("id") and (s.get("vendor") or s.get("vendor_id")):
            overrides[s["id"]] = s
    out = []
    for r in plan["rows"]:
        o = overrides.get(r["id"])
        row = {**r, "vendor": (o.get("vendor") if o and o.get("vendor")
                               else r["default_vendor"])}
        if o and o.get("vendor_id"):
            row["vendor_id"] = str(o["vendor_id"])
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Excess line for the allocation flow (negative, no-VAT, on the repairer PO)
# ---------------------------------------------------------------------------

def compute_excess(
    report_dict: dict,
    allocated_rows: list[dict],
    excess_pct: float | None,
    excess_min: float | None,
    excess_amount: float | None = None,
) -> dict | None:
    """Excess on the repairer's PO as a NEGATIVE, no-VAT line. Amount priority:
    ① typed amount → ② assessment's own Excess → ③ max(pct × repairer base,
    min). Minimum floor applied only when excess_min is explicitly given.
    Returns {vendor, amount_incl, line} or None when there is no excess /
    no repairer row."""
    rep_vendor = next((r["vendor"] for r in allocated_rows if r["id"] == "labour"), None)
    summary = report_dict.get("summary") or {}
    amount_incl = float(summary.get("Excess") or 0)
    if rep_vendor:
        base_excl = sum(r["amount"] for r in allocated_rows if r["vendor"] == rep_vendor)
        pct = (excess_pct if excess_pct is not None else 5.0) / 100.0
        minimum = excess_min if excess_min is not None else 5000.0
        if amount_incl <= 0:
            amount_incl = round(max(base_excl * pct, minimum), 2)
        elif excess_min is not None:
            amount_incl = round(max(amount_incl, float(excess_min)), 2)
    # directly-typed amount overrides everything.
    if excess_amount is not None and float(excess_amount) > 0:
        amount_incl = round(float(excess_amount), 2)
    if amount_incl <= 0 or not rep_vendor:
        return None
    return {
        "vendor": rep_vendor,
        "amount_incl": amount_incl,
        "line": {
            "description": (f"Less excess P{amount_incl:,.2f} — payable by the client "
                            f"to the repairer before vehicle release"),
            "qty": 1.0,
            "unit_price": -round(amount_incl, 2),
            "no_vat": True,
        },
    }
