"""assessment_report.build_report — MotoLink itemised assessment -> the exact
report_json shape procurement.claims_parser produces, so the existing claims-PO
engine (build_allocation_plan / claims_split / compute_excess / create_pos) runs
on it unchanged. Pure: no Django, no DB."""

import unittest

from claims_automation.assessment_report import build_report


def _n(**over):
    base = {
        "claimNumber": "G2026000123",
        "assessmentId": "ML-9",
        "registration": "B123ABC",
        "make": "TOYOTA",
        "model": "HILUX",
        "repairer": "Rolling Wheels",
        "summary": {"Excess": 3500.0},
        "finalCost": 20000.0,
        "lines": [
            {
                "type": "part",
                "code": "P1",
                "description": "Bumper",
                "supplier": "Motor Centre Toyota",
                "qty": 1,
                "unitPrice": 1000,
                "total": 1100,
            },
            {
                "type": "part",
                "code": "P2",
                "description": "Grille",
                "supplier": " motor  centre toyota ",
                "qty": 2,
                "unitPrice": "500.00",
                "total": "1,000.00",
            },
            {
                "type": "part",
                "code": "P3",
                "description": "Lamp",
                "supplier": "CFAO",
                "qty": 0,
                "unitPrice": 300,
                "total": 300,
            },
            {
                "type": "labour",
                "code": "L1",
                "description": "Strip and fit",
                "qty": 4,
                "unitPrice": 250,
                "total": 1000,
            },
            {"type": "paint", "code": "PT", "description": "Paint", "total": 800},
            {
                "type": "part",
                "code": "Z",
                "description": "Nothing",
                "supplier": "X",
                "qty": 1,
                "unitPrice": 0,
                "total": 0,
            },
        ],
    }
    base.update(over)
    return base


class BuildReportTests(unittest.TestCase):
    def test_header_fields(self):
        r = build_report(_n())
        self.assertEqual(r["claim_no"], "G2026000123")
        self.assertEqual(r["assessment_no"], "ML-9")
        self.assertEqual(r["vehicle_reg"], "B123ABC")
        self.assertEqual(r["vehicle"], "TOYOTA HILUX")
        self.assertEqual(r["repairer"], "Rolling Wheels")
        self.assertEqual(r["currency"], "BWP")
        self.assertEqual(r["summary"], {"Excess": 3500.0})
        self.assertIsNone(r["policy_no"])
        self.assertIsNone(r["client_name"])
        self.assertEqual(r["raw_text"], "")

    def test_parts_grouped_by_normalised_supplier_first_label_kept(self):
        r = build_report(_n())
        parts = [g for g in r["groups"] if g["kind"] == "parts"]
        self.assertEqual(
            [g["supplier_label"] for g in parts], ["Motor Centre Toyota", "CFAO"]
        )
        self.assertEqual(len(parts[0]["lines"]), 2)

    def test_part_line_shape_and_markup(self):
        r = build_report(_n())
        line = r["groups"][0]["lines"][0]
        self.assertEqual(
            set(line),
            {"code", "description", "supplier", "qty", "unit_price", "total", "markup"},
        )
        self.assertEqual(line["markup"], 100.0)
        second = r["groups"][0]["lines"][1]
        self.assertEqual(second["total"], 1000.0)
        self.assertEqual(second["unit_price"], 500.0)
        self.assertEqual(second["markup"], 0.0)

    def test_zero_qty_defaults_to_one(self):
        r = build_report(_n())
        cfao = [g for g in r["groups"] if g["supplier_label"] == "CFAO"][0]
        self.assertEqual(cfao["lines"][0]["qty"], 1.0)

    def test_zero_lines_skipped(self):
        r = build_report(_n())
        labels = [g["supplier_label"] for g in r["groups"]]
        self.assertNotIn("X", labels)

    def test_labour_group_last_under_repairer(self):
        r = build_report(_n())
        last = r["groups"][-1]
        self.assertEqual(last["kind"], "labour")
        self.assertEqual(last["supplier_label"], "Rolling Wheels")
        self.assertEqual(len(last["lines"]), 2)
        self.assertEqual(
            set(last["lines"][0]), {"code", "description", "units", "rate", "total"}
        )
        paint = last["lines"][1]
        self.assertEqual(paint["units"], 1.0)
        self.assertEqual(paint["rate"], 800.0)

    def test_labour_without_repairer_gets_placeholder(self):
        r = build_report(_n(repairer=""))
        self.assertEqual(r["groups"][-1]["supplier_label"], "Repairer (Labour)")

    def test_blank_supplier_part_goes_to_repairer_parts_group(self):
        r = build_report(
            _n(
                lines=[
                    {
                        "type": "part",
                        "code": "A",
                        "description": "Clip",
                        "supplier": "",
                        "qty": 1,
                        "unitPrice": 10,
                        "total": 10,
                    }
                ]
            )
        )
        self.assertEqual(r["groups"][0]["supplier_label"], "Rolling Wheels")
        self.assertEqual(r["groups"][0]["kind"], "parts")

    def test_group_totals(self):
        r = build_report(_n())
        g = r["groups"][0]
        self.assertEqual(g["subtotal_excl"], 2100.0)
        self.assertEqual(g["vat"], 294.0)
        self.assertEqual(g["total_incl"], 2394.0)

    def test_unknown_type_is_a_part(self):
        r = build_report(
            _n(
                lines=[
                    {
                        "type": "mystery",
                        "code": "M",
                        "description": "M",
                        "supplier": "S",
                        "qty": 1,
                        "unitPrice": 5,
                        "total": 5,
                    }
                ]
            )
        )
        self.assertEqual(r["groups"][0]["kind"], "parts")

    def test_no_lines_means_no_groups(self):
        self.assertEqual(build_report(_n(lines=[]))["groups"], [])
        self.assertEqual(build_report(_n(lines=None))["groups"], [])

    def test_excess_or_negative_lines_never_become_po_lines(self):
        r = build_report(_n(lines=[
            {"type": "part", "code": "EX", "description": "Excess", "supplier": "S", "qty": 1, "unitPrice": -3500, "total": -3500},
            {"type": "mystery", "code": "X1", "description": "Less policy excess", "supplier": "S", "total": 3500},
            {"type": "part", "code": "P", "description": "Bumper", "supplier": "S", "qty": 1, "unitPrice": 10, "total": 10},
        ]))
        lines = [l for g in r["groups"] for l in g["lines"]]
        self.assertEqual([l["code"] for l in lines], ["P"])

    def test_missing_make_model(self):
        self.assertIsNone(build_report(_n(make="", model=""))["vehicle"])


if __name__ == "__main__":
    unittest.main()
