"""assessment_report.build_report — MotoLink itemised assessment -> the exact
report_json shape procurement.claims_parser produces, so the existing claims-PO
engine (build_allocation_plan / claims_split / compute_excess / create_pos) runs
on it unchanged. Pure: no Django, no DB.
"""

from decimal import Decimal, ROUND_HALF_UP


def _to_decimal(value, default="0"):
    """Convert non-Decimal inputs via str() first, stripping grouping commas."""
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    s = str(value).replace(",", "").strip()
    if not s:
        return Decimal(default)
    return Decimal(s)


def _quantize_money(dec):
    """Round decimal money to 2 decimal places using ROUND_HALF_UP."""
    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_report(assessment):
    """Build a report_json-shaped dict from a MotoLink assessment payload."""
    data = assessment or {}
    make = data.get("make", "")
    model = data.get("model", "")
    repairer = data.get("repairer", "")

    vehicle = f"{make} {model}".strip() if make and model else None

    report = {
        "claim_no": data.get("claimNumber"),
        "assessment_no": data.get("assessmentId"),
        "vehicle_reg": data.get("registration"),
        "vehicle": vehicle,
        "repairer": repairer,
        "currency": "BWP",
        "summary": data.get("summary"),
        "policy_no": data.get("policy_no"),
        "client_name": data.get("client_name"),
        "raw_text": "",
        "groups": [],
    }

    lines = data.get("lines") or []
    part_groups = {}
    labour_lines = []
    labour_total = Decimal("0")
    labour_supplier_label = repairer if repairer else "Repairer (Labour)"

    for line in lines:
        if not isinstance(line, dict):
            continue
        # The excess is NEVER a line on a claims PO (house rule): the claims-PO
        # engine deducts it as a negative on the repairer PO from summary.Excess.
        # So an itemised "excess" row, or any negative row, is dropped here.
        label = f"{line.get('code') or ''} {line.get('description') or ''}".lower()
        if "excess" in label or _to_decimal(line.get("total"), "0") < 0:
            continue
        line_type = line.get("type", "part")

        if line_type in ("labour", "paint", "sundry", "sundries"):
            # Labour and paint lines belong in the labour group.
            qty_raw = line.get("qty")
            qty = _to_decimal(qty_raw, "1")
            if qty == Decimal("0"):
                qty = Decimal("1")

            unit_price_raw = line.get("unitPrice")
            if unit_price_raw is None or unit_price_raw == "":
                rate = _to_decimal(line.get("total"), "0")
            else:
                rate = _to_decimal(unit_price_raw, "0")

            total = _to_decimal(line.get("total"), "0")

            labour_lines.append(
                {
                    "code": line.get("code"),
                    "description": line.get("description"),
                    "units": float(qty),
                    "rate": float(_quantize_money(rate)),
                    "total": float(_quantize_money(total)),
                }
            )
            labour_total += total
        else:
            # Parts (and any unknown type) are grouped by normalized supplier.
            total = _to_decimal(line.get("total"), "0")
            if total == Decimal("0"):
                # Zero-value lines are skipped entirely.
                continue

            qty_raw = line.get("qty")
            qty = _to_decimal(qty_raw, "1")
            if qty == Decimal("0"):
                qty = Decimal("1")

            unit_price = _to_decimal(line.get("unitPrice"), "0")
            markup = total - unit_price * qty

            supplier = line.get("supplier", "")
            supplier_stripped = supplier.strip()

            if supplier_stripped:
                normalized = " ".join(supplier_stripped.lower().split())
                label = supplier
            else:
                normalized = "__blank__"
                label = repairer if repairer else "Repairer (Parts)"

            if normalized not in part_groups:
                part_groups[normalized] = {
                    "kind": "parts",
                    "supplier_label": label,
                    "lines": [],
                    "subtotal_excl": Decimal("0"),
                }

            part_groups[normalized]["lines"].append(
                {
                    "code": line.get("code"),
                    "description": line.get("description"),
                    "supplier": supplier,
                    "qty": float(qty),
                    "unit_price": float(_quantize_money(unit_price)),
                    "total": float(_quantize_money(total)),
                    "markup": float(_quantize_money(markup)),
                }
            )
            part_groups[normalized]["subtotal_excl"] += total

    # Output groups in order of first appearance: parts groups, then labour.
    for group in part_groups.values():
        subtotal = _quantize_money(group["subtotal_excl"])
        vat = _quantize_money(subtotal * Decimal("0.14"))
        total_incl = _quantize_money(subtotal + vat)
        report["groups"].append(
            {
                "kind": group["kind"],
                "supplier_label": group["supplier_label"],
                "lines": group["lines"],
                "subtotal_excl": float(subtotal),
                "vat": float(vat),
                "total_incl": float(total_incl),
            }
        )

    if labour_lines:
        subtotal = _quantize_money(labour_total)
        vat = _quantize_money(subtotal * Decimal("0.14"))
        total_incl = _quantize_money(subtotal + vat)
        report["groups"].append(
            {
                "kind": "labour",
                "supplier_label": labour_supplier_label,
                "lines": labour_lines,
                "subtotal_excl": float(subtotal),
                "vat": float(vat),
                "total_incl": float(total_incl),
            }
        )

    return report
