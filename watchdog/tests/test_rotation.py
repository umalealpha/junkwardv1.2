from django.test import SimpleTestCase

from watchdog.rotation import ALWAYS_ON_CATEGORIES, focus_for, is_full_sweep


class RotationTests(SimpleTestCase):
    def test_monday_is_finance_core(self):
        label, modules = focus_for(0)
        self.assertEqual(label, "Finance Core")
        self.assertIn("ledger", modules)
        self.assertIn("leases", modules)

    def test_thursday_is_insurance_ops(self):
        label, modules = focus_for(3)
        self.assertEqual(label, "Insurance Operations")
        self.assertIn("claims", modules)

    def test_sunday_is_full_sweep(self):
        label, modules = focus_for(6)
        self.assertEqual(label, "Full System Sweep")
        self.assertTrue(is_full_sweep(modules))

    def test_weekday_is_not_full_sweep(self):
        _, modules = focus_for(0)
        self.assertFalse(is_full_sweep(modules))

    def test_unknown_weekday_defaults_to_full_sweep(self):
        _, modules = focus_for(99)
        self.assertTrue(is_full_sweep(modules))

    def test_always_on_categories(self):
        # The CFO's "every night" promises must all be always-on.
        for c in ("integrity", "business_rule", "bug", "machine_talk"):
            self.assertIn(c, ALWAYS_ON_CATEGORIES)
