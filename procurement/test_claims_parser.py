"""
Tests for procurement/claims_parser.py (Phase 3 port — assessment PDF parser).

The deterministic rows→structure logic (parse_extracted) is exercised on a
synthetic fixture — header TEXT + raw table rows exactly as pdfplumber's
extract_tables() would hand them over — so no real PDF (and no DB) is needed.
Pure functions → SimpleTestCase.
"""
from django.test import SimpleTestCase

from procurement import claims_parser
from procurement.claims_engine import claims_split
from procurement.claims_parser import (
    _money,
    is_assessment_report,
    parse,
    parse_extracted,
)

# Header text as the assessment prints it: "Name: <Work Provider> Name:
# <Repairer>" on ONE line; Insured = second Name under "Assessor Insured".
FIXTURE_TEXT = """Alpha Direct Assessment Report
Claim: G2026004607 Assessment: ASS-889
Policy Number: MIS2026/12345
Name: Alpha Direct Insurance Name: Carfil Panel Beaters
Vehicle: Toyota Hilux 2.8 GD-6
Registration: B123ABC
Assessor Insured
Name: J. Assessor Name: Thabo Client
Cell: 71234567
Parts and Supplier detail follows.
"""

# Parts table rows (9 columns: code, desc, supplier, _, qty, unit, _, _, total).
# Money is printed with thousands commas + 2 decimals, as _money expects.
PARTS_ROWS = [
    ["P1", "Front Bumper", "Motor Holdings", "", "1.00", "1,500.00", "", "", "2,000.00"],  # 500 markup
    ["P2", "Grille", "Motor Holdings", "", "2.00", "250.00", "", "", "500.00"],
    ["", "", "", "", "", "", "", "", "2,500.00"],  # trailing totals row → skipped
]

# Labour table rows (5 columns: code, desc, units, rate, total).
LABOUR_ROWS = [
    ["L1", "Panel beating", "10.00", "150.00", "1,500.00"],
    ["L2", "POLISH & GLAZE", "0.00", "0.00", "1,600.00"],   # lump-sum row, units=0
    ["", "", "", "", "3,100.00"],                            # footer total → skipped
]

SUMMARY_ROWS = [
    ["Paint:", "1,200.00"],
    ["Sundries", "150.00"],
    ["Excess", "3,500.00"],
]


def _parse_fixture():
    return parse_extracted(FIXTURE_TEXT, PARTS_ROWS, LABOUR_ROWS, SUMMARY_ROWS).to_dict()


class ParseExtractedTests(SimpleTestCase):

    def test_header_meta(self):
        d = _parse_fixture()
        self.assertEqual(d["claim_no"], "G2026004607")
        self.assertEqual(d["assessment_no"], "ASS-889")
        self.assertEqual(d["policy_no"], "MIS2026/12345")
        self.assertEqual(d["repairer"], "Carfil Panel Beaters")
        self.assertEqual(d["work_provider"], "Alpha Direct Insurance")
        self.assertEqual(d["client_name"], "Thabo Client")
        self.assertEqual(d["client_contact"], "71234567")
        self.assertEqual(d["vehicle"], "Toyota Hilux 2.8 GD-6")
        self.assertEqual(d["vehicle_reg"], "B123ABC")

    def test_parts_grouped_by_supplier_with_derived_markup(self):
        d = _parse_fixture()
        parts = [g for g in d["groups"] if g["kind"] == "parts"]
        self.assertEqual(len(parts), 1)
        g = parts[0]
        self.assertEqual(g["supplier_label"], "Motor Holdings")
        self.assertEqual(g["subtotal_excl"], 2500.0)          # 2000 + 500
        self.assertEqual(len(g["lines"]), 2)                  # totals row skipped
        bumper = g["lines"][0]
        self.assertEqual(bumper["qty"], 1.0)
        self.assertEqual(bumper["unit_price"], 1500.0)
        self.assertEqual(bumper["total"], 2000.0)
        self.assertEqual(bumper["markup"], 500.0)             # derived: total - qty*unit
        self.assertEqual(g["lines"][1]["markup"], 0.0)        # 2 x 250 = 500, no markup

    def test_labour_goes_to_repairer_including_lump_sum_rows(self):
        d = _parse_fixture()
        labour = [g for g in d["groups"] if g["kind"] == "labour"]
        self.assertEqual(len(labour), 1)
        g = labour[0]
        self.assertEqual(g["supplier_label"], "Carfil Panel Beaters")  # explicit Repairer wins
        self.assertEqual(g["subtotal_excl"], 3100.0)          # 1500 + 1600 lump sum kept
        polish = g["lines"][1]
        self.assertEqual(polish["units"], 0.0)
        self.assertEqual(polish["total"], 1600.0)             # units=0 row NOT dropped

    def test_summary_keys_stripped_and_parsed(self):
        d = _parse_fixture()
        self.assertEqual(d["summary"], {"Paint": 1200.0, "Sundries": 150.0, "Excess": 3500.0})

    def test_labour_falls_back_to_repairer_looking_supplier(self):
        # No "Name: x Name: y" / "Repairer:" in text → hint on the supplier name.
        text = "Assessment Report\nClaim: B915BEL\n"
        rows = [["P1", "Fender", "Rolling Wheels Panel Beaters", "", "1.00", "800.00", "", "", "800.00"]]
        d = parse_extracted(text, rows, LABOUR_ROWS, []).to_dict()
        labour = next(g for g in d["groups"] if g["kind"] == "labour")
        self.assertEqual(labour["supplier_label"], "Rolling Wheels Panel Beaters")

    def test_output_feeds_phase1_engine(self):
        # End-to-end shape check: the Phase-1 money engine consumes the dict.
        s = claims_split(_parse_fixture())
        # Parts PO at BASE price (1x1500 + 2x250 = 2000) — the 500 embedded
        # markup is the repairer's separate Markup line, NOT part of the
        # parts block (it used to be double-counted in the grand total).
        self.assertEqual(s["motor_centre"]["parts_total"], 2000.0)
        # labour 3100 + paint 1200 + sundries 150 + markup 500 = 4950 gross
        self.assertEqual(s["specialised"]["gross_excl"], 4950.0)
        self.assertEqual(s["specialised"]["excess"], 3500.0)  # assessment excess kept
        # Markup is counted exactly ONCE in the grand total.
        self.assertEqual(s["grand"]["excl"],
                         round(2000.0 + 4950.0 - 3500.0, 2))

    def test_empty_extraction_yields_empty_report(self):
        # Malformed/empty PDF text: no crash, no groups, engine still safe.
        d = parse_extracted("", [], [], []).to_dict()
        self.assertEqual(d["groups"], [])
        self.assertEqual(d["summary"], {})
        s = claims_split(d)
        self.assertEqual(s["specialised"]["excess"], 0)      # no phantom excess
        self.assertEqual(s["grand"]["incl"], 0.0)


class HelperTests(SimpleTestCase):

    def test_money_parses_pula_commas_and_parens(self):
        self.assertEqual(_money("P 12,345.67"), 12345.67)
        self.assertEqual(_money("(5,000.00)"), 5000.0)
        self.assertEqual(_money("n/a"), 0.0)
        self.assertEqual(_money(None), 0.0)

    def test_is_assessment_report_heuristic(self):
        self.assertTrue(is_assessment_report(FIXTURE_TEXT + "\nParts Supplier"))
        self.assertFalse(is_assessment_report("Plain tax invoice"))


class ImportSmokeTests(SimpleTestCase):

    def test_parse_importable_and_callable(self):
        self.assertTrue(callable(parse))
        self.assertTrue(callable(claims_parser._extract))
