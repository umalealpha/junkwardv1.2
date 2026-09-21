"""exceptions/tests.py — placeholder smoke tests."""

from django.test import TestCase


class ExceptionsSmokeTests(TestCase):
    def test_module_imports(self):
        from exceptions import models, services, signals
        self.assertTrue(hasattr(models, 'Exception'))
        self.assertTrue(hasattr(services, 'create_exception'))
        self.assertTrue(hasattr(signals, 'register_signals'))
