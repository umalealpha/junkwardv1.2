"""repudiation_screen.screen — deterministic policy-breach flags from the claim
facts, the customer's answers and the text read off documents (police report).
A flag never declines anything: it only puts a drafted decline in front of the
claims manager. Pure: no Django."""

import unittest

from claims_automation.repudiation_screen import screen


def _facts(**over):
    base = {
        "date_of_loss": "2026-09-10",
        "reported_on": "2026-09-12",
        "policy_inception": "2026-01-01",
        "policy_expiry": "2026-12-31",
        "answers": {"driver_licence": "L123456", "driver_permission": "yes"},
        "documents_text": "",
    }
    base.update(over)
    return base


def codes(flags):
    return [f["code"] for f in flags]


class ScreenTests(unittest.TestCase):
    def test_clean_claim_has_no_flags(self):
        self.assertEqual(screen(_facts()), [])

    def test_flag_shape(self):
        f = screen(_facts(documents_text="Driver was drunk at the scene."))[0]
        self.assertEqual(set(f), {"code", "label", "detail"})
        self.assertTrue(f["label"])

    def test_intoxication_from_police_text(self):
        self.assertIn(
            "intoxication",
            codes(
                screen(
                    _facts(
                        documents_text="Breathalyser test positive, driver under the influence"
                    )
                )
            ),
        )

    def test_intoxication_from_answers(self):
        self.assertIn(
            "intoxication",
            codes(
                screen(
                    _facts(
                        answers={
                            "driver_licence": "L1",
                            "loss_description": "He had taken alcohol",
                        }
                    )
                )
            ),
        )

    def test_negated_alcohol_is_not_a_flag(self):
        self.assertEqual(
            codes(
                screen(_facts(documents_text="No alcohol involved. Driver not drunk."))
            ),
            [],
        )

    def test_police_form_negative_after_the_word_is_not_a_flag(self):
        for text in ("Alcohol: No", "Alcohol test negative", "Suspected alcohol: nil"):
            self.assertEqual(codes(screen(_facts(documents_text=text))), [], text)

    def test_loss_before_cover(self):
        self.assertIn(
            "loss_before_cover", codes(screen(_facts(date_of_loss="2025-12-30")))
        )

    def test_loss_after_expiry(self):
        self.assertIn(
            "loss_after_expiry",
            codes(screen(_facts(date_of_loss="2027-01-02", reported_on="2027-01-03"))),
        )

    def test_unlicensed_from_blank_answer(self):
        self.assertIn(
            "unlicensed_driver",
            codes(
                screen(
                    _facts(
                        answers={"driver_licence": " n/a ", "driver_permission": "yes"}
                    )
                )
            ),
        )

    def test_unlicensed_from_police_text(self):
        self.assertIn(
            "unlicensed_driver",
            codes(screen(_facts(documents_text="The driver had no driver's licence."))),
        )

    def test_missing_licence_key_is_not_a_flag(self):
        self.assertEqual(codes(screen(_facts(answers={}))), [])

    def test_unauthorised_driver(self):
        self.assertIn(
            "unauthorised_driver",
            codes(
                screen(
                    _facts(answers={"driver_licence": "L1", "driver_permission": "No"})
                )
            ),
        )

    def test_late_report_over_30_days(self):
        self.assertIn("late_report", codes(screen(_facts(reported_on="2026-10-11"))))
        self.assertNotIn("late_report", codes(screen(_facts(reported_on="2026-10-10"))))

    def test_bad_or_missing_dates_are_skipped(self):
        self.assertEqual(
            codes(
                screen(
                    _facts(
                        date_of_loss=None, reported_on="garbage", policy_inception=""
                    )
                )
            ),
            [],
        )

    def test_order_is_stable(self):
        flags = screen(
            _facts(
                date_of_loss="2025-11-01",
                reported_on="2026-01-15",
                answers={"driver_licence": "", "driver_permission": "no"},
                documents_text="driver was intoxicated",
            )
        )
        self.assertEqual(
            codes(flags),
            [
                "loss_before_cover",
                "intoxication",
                "unlicensed_driver",
                "unauthorised_driver",
                "late_report",
            ],
        )

    def test_none_inputs_are_safe(self):
        self.assertEqual(screen({}), [])


if __name__ == "__main__":
    unittest.main()
