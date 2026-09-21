from django.test import SimpleTestCase

from watchdog import checks as check_mod
from watchdog.checks import Check

_MH = "wd_mh_probe"     # a temp module_health check to prove rotation gating


class SelectTests(SimpleTestCase):
    def setUp(self):
        # A module_health check is the kind the rotation gates by day. (Integrity /
        # business-rule / bug / machine-talk are always-on by design.)
        check_mod.REGISTRY[_MH] = Check(
            key=_MH, label="probe", module="claims", category="module_health",
            run=lambda: [])
        self.addCleanup(lambda: check_mod.REGISTRY.pop(_MH, None))

    def _keys(self, weekday, extra=()):
        return {c.key for c in check_mod.select(weekday, extra)}

    def test_always_on_included_every_day(self):
        # The CFO's "every night" checks run regardless of the day's focus.
        for wd in range(7):
            keys = self._keys(wd)
            self.assertIn("trial_balance", keys, wd)          # integrity
            self.assertIn("payment_dual_control", keys, wd)   # business_rule
            self.assertIn("bug_board", keys, wd)              # bug
            self.assertIn("machine_talk", keys, wd)           # machine_talk

    def test_module_health_included_on_its_rotation_day(self):
        # Thursday (3) = Insurance Operations -> the claims deep check runs.
        self.assertIn(_MH, self._keys(3))

    def test_module_health_excluded_off_its_day(self):
        # Monday (0) = Finance Core -> the claims deep check does NOT run.
        self.assertNotIn(_MH, self._keys(0))

    def test_extra_modules_pull_a_module_health_check_in(self):
        # MACHINE-TALK says claims changed today -> tested even on Monday.
        self.assertIn(_MH, self._keys(0, ("claims",)))

    def test_full_sweep_includes_everything(self):
        keys = self._keys(6)  # Sunday
        self.assertIn(_MH, keys)
        self.assertIn("commissions_gl_link", keys)
        self.assertIn("leases_gl_link", keys)
        self.assertIn("claims_gl_link", keys)
