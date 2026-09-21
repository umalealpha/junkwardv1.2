"""ELRA-2025 leave-rule tests (HRIS blueprint, ref ADI/HC/HRIS/2026).

Locks the statutory correctness (maternity 14wk/98d @70%, paternity exists,
annual accrues monthly) and the DB-overlay behaviour (HR edits LeaveType in
admin -> get_leave_rules picks it up, no code deploy), while proving defaults
still hold when the table is empty and that non-model fields (gender) survive
the overlay.
"""
from django.core.management import call_command
from django.test import TestCase

from hris.feature_views import DEFAULT_LEAVE_RULES, get_leave_rules
from hris.models import LeaveType


class ElraDefaultsTest(TestCase):
    def test_maternity_is_elra_not_old_cos(self):
        m = DEFAULT_LEAVE_RULES["maternity"]
        self.assertEqual(m["days"], 98)          # 14 weeks, ELRA s.222 (was 84)
        self.assertEqual(m["paid_pct"], 70)      # ELRA 70% (was 50)
        self.assertTrue(m["blocks_termination"])  # ELRA s.224
        self.assertEqual(m["gender"], "F")

    def test_paternity_and_annual_and_hospitalisation(self):
        self.assertEqual(DEFAULT_LEAVE_RULES["paternity"]["days"], 5)        # ELRA s.227
        self.assertEqual(DEFAULT_LEAVE_RULES["annual"]["accrual_method"], "monthly")
        self.assertEqual(DEFAULT_LEAVE_RULES["annual"]["statutory_min"], 15)
        self.assertEqual(DEFAULT_LEAVE_RULES["sick"]["accrual_method"], "frontload")
        self.assertIn("hospitalisation", DEFAULT_LEAVE_RULES)


class GetLeaveRulesOverlayTest(TestCase):
    def test_defaults_when_db_empty(self):
        rules = get_leave_rules()
        self.assertEqual(rules["maternity"]["days"], 98)
        self.assertEqual(rules["sick"]["accrual_method"], "frontload")

    def test_db_row_overrides_default_without_code_change(self):
        LeaveType.objects.create(
            code="annual", name="Annual Leave", default_annual_days=25, paid_pct=100,
            accrual_method="monthly", statutory_ref="ELRA s.219 (BW custom)", is_active=True)
        rules = get_leave_rules()
        self.assertEqual(rules["annual"]["days"], 25)               # HR edit, no deploy
        self.assertEqual(rules["annual"]["cos"], "ELRA s.219 (BW custom)")

    def test_overlay_preserves_non_model_defaults(self):
        # LeaveType has no gender column; the overlay must keep gender + the
        # termination block from the frozen default for maternity.
        LeaveType.objects.create(
            code="maternity", name="Maternity Leave", default_annual_days=98,
            paid_pct=70, is_active=True)
        rules = get_leave_rules()
        self.assertEqual(rules["maternity"]["gender"], "F")
        self.assertTrue(rules["maternity"]["blocks_termination"])

    def test_inactive_row_is_ignored(self):
        LeaveType.objects.create(
            code="annual", name="Annual", default_annual_days=99, paid_pct=100,
            is_active=False)
        self.assertEqual(get_leave_rules()["annual"]["days"], 21)   # falls back to default

    def test_legacy_case_variant_does_not_clobber_elra(self):
        # Real prod data: a stale UPPERCASE "MATERNITY" (84@50, no ELRA ref)
        # coexists with the seeded "maternity" (98@70, ELRA). Both lower-case to
        # the same key; the ELRA row must win regardless of insertion order.
        LeaveType.objects.create(
            code="MATERNITY", name="Maternity", default_annual_days=84, paid_pct=50,
            statutory_ref="", is_active=True)
        LeaveType.objects.create(
            code="maternity", name="Maternity Leave", default_annual_days=98, paid_pct=70,
            statutory_ref="ELRA s.222", is_active=True)
        m = get_leave_rules()["maternity"]
        self.assertEqual(m["days"], 98)
        self.assertEqual(m["paid_pct"], 70)

    def test_non_canonical_payroll_codes_not_surfaced(self):
        # The payroll leave taxonomy (vac_driver, hosp_half, lwop, …) must NOT
        # leak onto the staff leave page via the rule set.
        LeaveType.objects.create(
            code="vac_driver", name="Driver Vacation", default_annual_days=18,
            paid_pct=100, is_active=True)
        rules = get_leave_rules()
        self.assertNotIn("vac_driver", rules)
        self.assertEqual(set(rules), set(DEFAULT_LEAVE_RULES))


class SeedCommandTest(TestCase):
    def test_seed_populates_elra_values(self):
        call_command("seed_elra_leave")
        rules = get_leave_rules()
        self.assertEqual(rules["maternity"]["days"], 98)
        self.assertEqual(rules["maternity"]["paid_pct"], 70)
        self.assertEqual(rules["paternity"]["cos"], "ELRA s.227")
        self.assertIn("hospitalisation", rules)
        self.assertGreaterEqual(LeaveType.objects.filter(is_active=True).count(), 8)
