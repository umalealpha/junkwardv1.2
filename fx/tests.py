"""fx/tests.py — placeholder smoke tests."""

from django.test import TestCase


class FXSmokeTests(TestCase):
    def test_module_imports(self):
        from fx import models, services
        self.assertTrue(hasattr(models, 'FXRevaluation'))
        self.assertTrue(hasattr(services, 'run_fx_revaluation'))
