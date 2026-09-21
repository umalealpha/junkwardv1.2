"""core/tests/test_route_manifest.py — a route must never vanish by accident.

THE BUG THIS CATCHES (2026-08-17): PR #676 branched from an older copy of
alpha_finance/api_router.py and its merge silently reverted four already-shipped
features' routes — cash flow (#672), 11-Year reinsurance history (#661), records
file-requests (Tshepo Maswabi), and the DPIA register. A fifth (expense-claims
mark-paid) was never mounted at all. Nobody removed them on purpose.

Why it took a day to find: each dropped route only showed up as a 404 inside its
OWN feature's tests, so CI reported 33 failures + 8 errors across unrelated apps.
The actual story — "one merge switched off five features" — was invisible.

This test makes that story ONE failure that names exactly what disappeared.

Removing a route is allowed — it just has to be deliberate. When a removal is
intended, regenerate the baseline (the failure message prints the command).

Run: python manage.py test core.tests.test_route_manifest
"""
from pathlib import Path

from django.test import SimpleTestCase

from core.route_manifest import all_route_names

BASELINE = Path(__file__).with_name("route_manifest_baseline.txt")

REGENERATE = (
    "python manage.py shell -c "
    "\"from core.route_manifest import all_route_names; "
    "from pathlib import Path; "
    "Path('core/tests/route_manifest_baseline.txt')"
    ".write_text(chr(10).join(sorted(all_route_names())) + chr(10))\""
)


class RouteManifestTests(SimpleTestCase):

    def test_no_route_has_silently_disappeared(self):
        baseline = {
            line.strip()
            for line in BASELINE.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        missing = sorted(baseline - all_route_names())
        self.assertEqual(
            missing, [],
            f"\n\n{len(missing)} route(s) that used to exist are GONE from the "
            f"URLConf:\n  " + "\n  ".join(missing) +
            "\n\nThe usual cause is a merge from a branch that predates them — "
            "check whether a recent PR reverted part of alpha_finance/"
            "api_router.py rather than assuming the routes were removed on "
            "purpose. Every feature behind these names is unreachable (404) "
            "until they are restored.\n\nIf the removal IS intended, "
            f"regenerate the baseline:\n  {REGENERATE}\n"
        )

    def test_baseline_is_not_stale_enough_to_be_useless(self):
        """A baseline far smaller than reality means someone regenerated it
        against a broken URLConf — the guard would then protect almost nothing."""
        baseline_count = len([
            line for line in BASELINE.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ])
        self.assertGreater(
            baseline_count, len(all_route_names()) * 0.9,
            "The route baseline is far smaller than the live URLConf — it was "
            "probably regenerated while routes were missing. Rebuild it from a "
            "known-good checkout."
        )
