from django.test import SimpleTestCase

from watchdog.severity import classify


class SeverityTests(SimpleTestCase):
    def test_finance_is_flagged(self):
        flagged, reason = classify(("finance",))
        self.assertTrue(flagged)
        self.assertIn("finance", reason)

    def test_permissions_is_flagged(self):
        flagged, _ = classify(("permissions",))
        self.assertTrue(flagged)

    def test_pii_is_flagged(self):
        flagged, _ = classify(("pii",))
        self.assertTrue(flagged)

    def test_payment_and_bank_are_flagged(self):
        self.assertTrue(classify(("payment",))[0])
        self.assertTrue(classify(("bank",))[0])

    def test_frozen_and_schema_are_flagged(self):
        self.assertTrue(classify(("frozen",))[0])
        self.assertTrue(classify(("schema",))[0])

    def test_safe_domain_not_flagged(self):
        flagged, reason = classify(("reporting",))
        self.assertFalse(flagged)
        self.assertEqual(reason, "")

    def test_empty_not_flagged(self):
        self.assertFalse(classify(())[0])
        self.assertFalse(classify(None)[0])

    def test_mixed_flags_and_lists_danger_domain(self):
        flagged, reason = classify(("reporting", "gl"))
        self.assertTrue(flagged)
        self.assertIn("gl", reason)
