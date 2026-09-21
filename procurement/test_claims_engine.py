"""
Regression tests for procurement/claims_engine.py (Phase 1 port).

Each test reproduces a rule Lemogang (Parts) / Kao (Claims) set by email, so
the ported arithmetic stays faithful to the (now-retired) po-invoice-ingest
engine. Pure functions → SimpleTestCase (no DB).
"""
from django.test import SimpleTestCase

from procurement.claims_engine import (
    VAT_RATE,
    apply_allocations,
    build_allocation_plan,
    claims_split,
    compute_excess,
    sa_supplier_default,
)


def _report(excess=0, paint=1000.0, sundries=200.0, labour=8000.0, parts_markup=0.0):
    """Assessment with one supplier-parts group + one labour group."""
    return {
        "repairer": "Carfil Panel Beaters",
        "summary": {"Paint": paint, "Sundries": sundries, "Excess": excess},
        "groups": [
            {"kind": "parts", "supplier_label": "Motor Holdings",
             "subtotal_excl": 2000.0 + parts_markup,
             "lines": [{"code": "P1", "description": "Bumper", "qty": 1,
                        "unit_price": 2000.0, "markup": parts_markup}]},
            {"kind": "labour", "supplier_label": "Carfil Panel Beaters",
             "subtotal_excl": labour, "lines": []},
        ],
    }


def _b915bel_report():
    """The real B915BEL assessment (Kao end-to-end test 2026-07-06):
    repairer "Rolling Wheels" supplies 6 of the parts ITSELF (no markup),
    "MOTOR CENTRE" supplies 2 mirrors (embedded markup 670.96)."""
    return {
        "repairer": "Rolling Wheels",
        "summary": {"Paint": 17981.41, "Sundries": 91.44, "Excess": 3500.0},
        "groups": [
            {"kind": "parts", "supplier_label": "ROLLING WHEELS",
             "subtotal_excl": 5788.84,          # repairer's own — NO markup
             "lines": [
                 {"description": "Bonnet",        "qty": 1, "unit_price": 1200.00},
                 {"description": "Grille",        "qty": 1, "unit_price": 950.50},
                 {"description": "Headlamp LH",   "qty": 1, "unit_price": 875.34},
                 {"description": "Bumper front",  "qty": 1, "unit_price": 1100.00},
                 {"description": "Fender LH",     "qty": 1, "unit_price": 823.00},
                 {"description": "Bracket set",   "qty": 1, "unit_price": 840.00},
             ]},
            {"kind": "parts", "supplier_label": "MOTOR CENTRE",
             "subtotal_excl": 3354.76,          # base 2,683.80 + markup 670.96
             "lines": [
                 {"description": "Mirror LH", "qty": 1, "unit_price": 1341.90},
                 {"description": "Mirror RH", "qty": 1, "unit_price": 1341.90},
             ]},
            {"kind": "labour", "supplier_label": "Rolling Wheels",
             "subtotal_excl": 9692.0, "lines": []},
        ],
    }


class ClaimsSplitTests(SimpleTestCase):

    def test_minimum_excess_floor_applied_when_no_assessment_excess(self):
        # Kao/Lemogang: excess below the minimum is lifted to the minimum.
        s = claims_split(_report(excess=0))  # 5% of 9200 = 460 < 5000 → 5000
        self.assertEqual(s["specialised"]["excess"], 5000.0)

    def test_no_vat_on_excess(self):
        # VAT is on the repair work only; excess comes off the incl total flat.
        s = claims_split(_report(excess=0))
        gross = s["specialised"]["gross_excl"]          # 9200
        self.assertEqual(s["specialised"]["vat"], round(gross * VAT_RATE, 2))  # 1288, not (gross-excess)*14%
        self.assertEqual(s["specialised"]["incl"],
                         round(gross + s["specialised"]["vat"] - s["specialised"]["excess"], 2))

    def test_typed_excess_amount_wins(self):
        # Kao 2026-06-29: a directly-typed amount overrides assessment + %/min.
        s = claims_split(_report(excess=0), excess_amount=3500.0)
        self.assertEqual(s["specialised"]["excess"], 3500.0)

    def test_assessment_excess_kept_when_no_minimum_entered(self):
        # No regression: with no explicit minimum, keep the assessment's excess.
        s = claims_split(_report(excess=3500.0))
        self.assertEqual(s["specialised"]["excess"], 3500.0)

    def test_minimum_floors_assessment_excess_only_when_entered(self):
        s = claims_split(_report(excess=3500.0), excess_min=5000.0)
        self.assertEqual(s["specialised"]["excess"], 5000.0)

    def test_parts_on_a_separate_block(self):
        # 2-PO split, simple case (repairer supplies NO parts): ALL parts sit
        # on the motor-centre block, apart from the repairer.
        s = claims_split(_report(excess=0))
        self.assertEqual(s["motor_centre"]["parts_total"], 2000.0)
        self.assertEqual(s["motor_centre"]["vat"], round(2000.0 * VAT_RATE, 2))
        self.assertNotIn("excess", s["motor_centre"])  # no excess on the parts PO
        self.assertEqual(s["specialised"]["repairer_parts"], 0.0)  # none of its own

    def test_repairer_own_parts_sit_on_the_repairer_block_b915bel(self):
        # BUGFIX (B915BEL, Kao end-to-end 2026-07-06): a parts group whose
        # supplier matches the repairer is billed on the REPAIRER PO — that is
        # how build_allocation_plan + create-pos bucket rows (by vendor). The
        # split preview must say the same, not shove ALL parts into the
        # motor-centre block.
        s = claims_split(_b915bel_report())
        spec = s["specialised"]
        self.assertEqual(spec["repairer_parts"], 5788.84)   # own 6 parts, base
        self.assertEqual(spec["labour"], 9692.0)
        self.assertEqual(spec["markup"], 670.96)            # supplier parts only
        # gross = own parts 5,788.84 + labour 9,692 + markup 670.96
        #       + paint 17,981.41 + sundries 91.44 = 34,224.65 (Kao's figure)
        self.assertEqual(spec["gross_excl"], 34224.65)
        self.assertEqual(spec["excess"], 3500.0)            # assessment's own
        self.assertEqual(spec["excl"], 30724.65)
        # VAT on the FULL repair work (34,224.65); excess deducted flat. Block
        # rounding: 34,224.65 x 14% = 4,791.451 -> 4,791.45 / incl 35,516.10.
        # (Kao's per-line PO shows 4,791.44 / 35,516.09 — a 1-cent per-line
        # quantisation difference; the actual PO totals are summed per line.)
        self.assertEqual(spec["vat"], round(34224.65 * VAT_RATE, 2))
        self.assertEqual(spec["incl"], round(34224.65 + spec["vat"] - 3500.0, 2))
        # Parts PO = the OUTSIDE supplier's 2 mirrors at base ONLY.
        mc = s["motor_centre"]
        self.assertEqual(mc["parts_total"], 2683.80)
        self.assertEqual(mc["excl"], 2683.80)
        self.assertEqual(mc["vat"], round(2683.80 * VAT_RATE, 2))
        self.assertEqual(mc["incl"], round(2683.80 + mc["vat"], 2))
        # Grand = repairer block + parts block (matches Kao's 38,575.63).
        self.assertEqual(s["grand"]["incl"], 38575.63)
        self.assertEqual(s["grand"]["excl"], round(spec["excl"] + mc["excl"], 2))

    def test_parts_block_is_base_price_markup_counted_once(self):
        # BUGFIX: parts_total used the group subtotal (base + embedded markup)
        # while the same markup ALSO sat on the repairer block — the grand
        # total double-counted it. Parts PO = BASE price only (rule 1).
        s = claims_split(_report(excess=0, parts_markup=500.0))
        self.assertEqual(s["motor_centre"]["parts_total"], 2000.0)   # base, not 2500
        self.assertEqual(s["specialised"]["markup"], 500.0)          # once, on repairer
        # grand excl = parts base + (labour+paint+markup+sundries) - excess
        self.assertEqual(
            s["grand"]["excl"],
            round(2000.0 + s["specialised"]["gross_excl"] - s["specialised"]["excess"], 2),
        )

    def test_split_markup_pct_base_excludes_repairer_parts(self):
        # BUGFIX (rule 4): the % markup base in the SPLIT must exclude parts
        # the repairer sourced itself — same base as build_allocation_plan.
        report = {
            "repairer": "Carfil Panel Beaters",
            "summary": {},
            "groups": [
                {"kind": "parts", "supplier_label": "Motor Holdings", "subtotal_excl": 2000.0,
                 "lines": [{"description": "Bumper", "qty": 1, "unit_price": 2000.0}]},
                {"kind": "parts", "supplier_label": "Carfil Panel Beaters (Pty) Ltd",
                 "subtotal_excl": 1000.0,
                 "lines": [{"description": "Filler", "qty": 1, "unit_price": 1000.0}]},
                {"kind": "labour", "supplier_label": "Carfil Panel Beaters",
                 "subtotal_excl": 4000.0, "lines": []},
            ],
        }
        s = claims_split(report, markup_pct=10.0)
        self.assertEqual(s["specialised"]["markup"], 200.0)   # 10% of 2000, NOT of 3000
        plan = build_allocation_plan(report, markup_pct=10.0)
        plan_markup = next(r for r in plan["rows"] if r["id"] == "markup")
        self.assertEqual(s["specialised"]["markup"], plan_markup["amount"])

    def test_split_markup_fallback_excludes_repairer_parts(self):
        # Fallback (no % typed): embedded markup on the REPAIRER's own parts
        # is never earned — split and allocation plan agree.
        report = {
            "repairer": "Carfil Panel Beaters",
            "summary": {},
            "groups": [
                {"kind": "parts", "supplier_label": "Motor Holdings", "subtotal_excl": 2000.0,
                 "lines": [{"description": "Bumper", "qty": 1, "unit_price": 1500.0,
                            "markup": 500.0}]},
                {"kind": "parts", "supplier_label": "Carfil Panel Beaters", "subtotal_excl": 1000.0,
                 "lines": [{"description": "Filler", "qty": 1, "unit_price": 900.0,
                            "markup": 100.0}]},
                {"kind": "labour", "supplier_label": "Carfil Panel Beaters",
                 "subtotal_excl": 4000.0, "lines": []},
            ],
        }
        s = claims_split(report)
        self.assertEqual(s["specialised"]["markup"], 500.0)   # MH's 500 only, not +100
        plan = build_allocation_plan(report)
        plan_markup = next(r for r in plan["rows"] if r["id"] == "markup")
        self.assertEqual(s["specialised"]["markup"], plan_markup["amount"])

    def test_split_includes_anti_corrosion_and_underside_paint(self):
        # BUGFIX: build_allocation_plan routes these extras to the repairer PO
        # but the split block omitted them — the displayed incl was short of
        # the PO actually created.
        report = _report(excess=3500.0)
        report["summary"]["Anti-Corrosion"] = 400.0
        report["summary"]["Underside Paint"] = 300.0
        s = claims_split(report)
        self.assertEqual(s["specialised"]["anti_corrosion"], 400.0)
        self.assertEqual(s["specialised"]["underside_paint"], 300.0)
        # gross = labour 8000 + paint 1000 + sundries 200 + 400 + 300
        self.assertEqual(s["specialised"]["gross_excl"], 9900.0)
        # ...and it equals the sum of the allocation rows the repairer PO gets.
        plan = build_allocation_plan(report)
        rep_rows_total = round(sum(
            r["amount"] for r in plan["rows"]
            if r["default_vendor"] == "Carfil Panel Beaters" and not r["id"].startswith("parts:")
        ), 2)
        self.assertEqual(s["specialised"]["gross_excl"], rep_rows_total)


class AllocationTests(SimpleTestCase):

    def _markup_report(self):
        # Supplier parts (markup earned) + repairer's OWN parts (no markup) + labour.
        return {
            "repairer": "Carfil Panel Beaters",
            "summary": {},
            "groups": [
                {"kind": "parts", "supplier_label": "Motor Holdings", "subtotal_excl": 2000.0,
                 "lines": [{"code": "P1", "description": "Bumper", "qty": 1,
                            "unit_price": 1500.0, "markup": 500.0}]},          # base 1500, +500 markup
                {"kind": "parts", "supplier_label": "Carfil Panel Beaters", "subtotal_excl": 1000.0,
                 "lines": [{"description": "Filler", "qty": 1, "unit_price": 900.0, "markup": 100.0}]},
                {"kind": "labour", "supplier_label": "Carfil Panel Beaters", "subtotal_excl": 4000.0,
                 "lines": []},
            ],
        }

    def test_markup_only_on_supplier_sourced_parts(self):
        # Lemogang 2026-06-22: markup NOT on the repairer's own parts.
        plan = build_allocation_plan(self._markup_report())
        markup = next(r for r in plan["rows"] if r["id"] == "markup")
        self.assertEqual(markup["amount"], 500.0)     # MH's 500 only, not Carfil's 100

    def test_markup_pct_base_excludes_repairer_parts(self):
        plan = build_allocation_plan(self._markup_report(), markup_pct=10.0)
        markup = next(r for r in plan["rows"] if r["id"] == "markup")
        self.assertEqual(markup["amount"], 200.0)     # 10% of 2000 supplier parts (not 3000)

    def test_parts_billed_at_base_price(self):
        plan = build_allocation_plan(self._markup_report())
        mh = next(r for r in plan["rows"] if r["id"] == "parts:Motor Holdings")
        self.assertEqual(mh["amount"], 1500.0)        # base, markup stripped to its own line

    def test_vendor_options_and_labour_row(self):
        plan = build_allocation_plan(self._markup_report())
        self.assertIn("Motor Holdings", plan["vendor_options"])
        self.assertIn("Carfil Panel Beaters", plan["vendor_options"])
        self.assertTrue(any(r["id"] == "labour" for r in plan["rows"]))


class ComputeExcessTests(SimpleTestCase):

    def _alloc(self):
        report = {
            "repairer": "Carfil Panel Beaters",
            "summary": {"Excess": 0},
            "groups": [
                {"kind": "parts", "supplier_label": "Motor Holdings", "subtotal_excl": 2000.0,
                 "lines": [{"description": "Bumper", "qty": 1, "unit_price": 2000.0}]},
                {"kind": "labour", "supplier_label": "Carfil Panel Beaters", "subtotal_excl": 4000.0,
                 "lines": []},
            ],
        }
        return report, apply_allocations(build_allocation_plan(report), None)

    def test_excess_is_negative_no_vat_line_on_repairer(self):
        report, allocated = self._alloc()
        ex = compute_excess(report, allocated, excess_pct=5.0, excess_min=5000.0)
        self.assertIsNotNone(ex)
        self.assertEqual(ex["vendor"], "Carfil Panel Beaters")
        self.assertLess(ex["line"]["unit_price"], 0)          # negative deduction line
        self.assertTrue(ex["line"]["no_vat"])                 # no VAT on the excess
        self.assertEqual(ex["amount_incl"], 5000.0)           # floor

    def test_typed_excess_overrides(self):
        report, allocated = self._alloc()
        ex = compute_excess(report, allocated, None, None, excess_amount=3500.0)
        self.assertEqual(ex["amount_incl"], 3500.0)
        self.assertEqual(ex["line"]["unit_price"], -3500.0)

    def test_pct_excess_matches_split_excess_incl_extras(self):
        # PARITY: on the %-path (no assessment excess, no typed amount) the
        # excess compute_excess produces for the PO must equal the excess
        # claims_split shows the user — including the summary extras.
        report = {
            "repairer": "Carfil Panel Beaters",
            "summary": {"Excess": 0, "Paint": 1000.0, "Sundries": 200.0,
                        "Anti-Corrosion": 400.0},
            "groups": [
                {"kind": "parts", "supplier_label": "Motor Holdings", "subtotal_excl": 2000.0,
                 "lines": [{"description": "Bumper", "qty": 1, "unit_price": 2000.0}]},
                {"kind": "labour", "supplier_label": "Carfil Panel Beaters",
                 "subtotal_excl": 200000.0, "lines": []},
            ],
        }
        allocated = apply_allocations(build_allocation_plan(report), None)
        ex = compute_excess(report, allocated, excess_pct=5.0, excess_min=5000.0)
        s = claims_split(report, excess_pct=0.05, excess_min=5000.0)
        self.assertEqual(ex["amount_incl"], 10080.0)          # 5% of 201,600
        self.assertEqual(ex["amount_incl"], s["specialised"]["excess"])

    def test_parts_only_assessment_returns_none(self):
        # No labour/repairer row → no PO can carry the excess; the engine
        # returns None and the API layer flags it (never silently dropped).
        report = {
            "repairer": "",
            "summary": {"Excess": 3000.0},
            "groups": [
                {"kind": "parts", "supplier_label": "Motor Holdings", "subtotal_excl": 2000.0,
                 "lines": [{"description": "Bumper", "qty": 1, "unit_price": 2000.0}]},
            ],
        }
        allocated = apply_allocations(build_allocation_plan(report), None)
        self.assertIsNone(compute_excess(report, allocated, 5.0, 5000.0))

    def test_apply_allocations_falls_back_to_default_vendor(self):
        plan = {"rows": [{"id": "labour", "default_vendor": "Rep", "amount": 1.0},
                         {"id": "paint",  "default_vendor": "Rep", "amount": 1.0}]}
        out = apply_allocations(plan, [{"id": "labour", "vendor": "Other"}])
        self.assertEqual(out[0]["vendor"], "Other")   # override applied
        self.assertEqual(out[1]["vendor"], "Rep")     # untouched row keeps default

    def test_apply_allocations_carries_vendor_id(self):
        # A saved entry may pin a real billing.Contact PK (picked on the
        # review screen) — carried through so create-pos resolves the row
        # directly, bypassing name matching.
        plan = {"rows": [{"id": "labour", "default_vendor": "Rep", "amount": 1.0}]}
        out = apply_allocations(
            plan, [{"id": "labour", "vendor": "Rep", "vendor_id": "abc-123"}])
        self.assertEqual(out[0]["vendor"], "Rep")
        self.assertEqual(out[0]["vendor_id"], "abc-123")
        # No vendor_id saved -> key absent (existing {id, vendor} behaviour).
        out2 = apply_allocations(plan, [{"id": "labour", "vendor": "Other"}])
        self.assertEqual(out2[0]["vendor"], "Other")
        self.assertNotIn("vendor_id", out2[0])
        # An EMPTY vendor_id (frontend sends '' when nothing is picked) is
        # ignored — never carried onto the row.
        out3 = apply_allocations(
            plan, [{"id": "labour", "vendor": "Rep", "vendor_id": ""}])
        self.assertNotIn("vendor_id", out3[0])


class SupplierDefaultTests(SimpleTestCase):

    def test_sa_suppliers_default_zar_no_vat(self):
        for name in ("CFAO Motors", "SMG", "Omega Autoworld", "Ace Auto"):
            d = sa_supplier_default(name)
            self.assertFalse(d["vat"])
            self.assertEqual(d["currency"], "ZAR")

    def test_plain_omega_stays_bwp_with_vat(self):
        # Conservative match: a plain BWP "OMEGA" is NOT flipped to ZAR.
        d = sa_supplier_default("OMEGA")
        self.assertTrue(d["vat"])
        self.assertEqual(d["currency"], "BWP")
