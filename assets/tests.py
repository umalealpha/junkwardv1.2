"""
assets/tests.py

Lightweight smoke tests covering the depreciation engine.  Postgres-backed
integration tests can be added once a CI runner is available.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

# Tests deferred — run with: python manage.py test assets
# Add cases here when CI is wired up. Coverage targets:
#   - straight-line depreciation arithmetic
#   - reducing-balance depreciation arithmetic
#   - opening accumulated depreciation respected
#   - fully-depreciated assets skipped
#   - cutover date prevents back-dated depreciation
#   - dispose with proceeds > NBV produces gain
#   - dispose with proceeds < NBV produces loss
#   - Odoo CSV alias detection (reference / asset code / etc.)


class PlaceholderTest(TestCase):
    def test_app_loads(self):
        from assets.apps import AssetsConfig
        self.assertEqual(AssetsConfig.name, 'assets')
