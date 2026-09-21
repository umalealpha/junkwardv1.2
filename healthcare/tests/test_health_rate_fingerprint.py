"""Drift tripwire for the broker PWA's bundled rate copy.

The broker quoting app (github.com/alphadirectinsurance/health-broker-pwa) ships a
COPY of these office rates in public/data/rate-table.json, exported verbatim from
healthcare/health_rates.py so it can quote offline. A copy is only guaranteed
correct AT EXPORT TIME — if Finance edits a rate here and nobody re-exports, the
broker app keeps quoting the OLD rate, green and silent.

This test pins a fingerprint of the office rate card. It goes RED the moment any
rate (or the VAT rate) changes. When it fails on an INTENTIONAL change:
  1. cd into the health-broker-pwa checkout,
  2. run  ALPHA_FINANCE=~/work/alpha-finance python3 scripts/export-rate-table.py
  3. `npm test` there, then redeploy the PWA (wrangler pages deploy dist),
  4. bump EXPECTED_FINGERPRINT / EXPECTED_VAT_RATE below to the new values.

That sequence is the ONLY way the fingerprint should ever change — so the bump is
the human confirming the broker app was refreshed.
"""
import hashlib
import json

from healthcare import health_rates as HR

# Bump ONLY together with a health-broker-pwa re-export + redeploy (see docstring).
EXPECTED_FINGERPRINT = "eee5827eb4ca2982ff2baff380b75c70390f706fe527ce1fea9887e825253060"
EXPECTED_VAT_RATE = "0.14"


def _fingerprint() -> str:
    """Stable sha256 of every office rate (str(Decimal) keeps exact thebe)."""
    payload = {
        tier: {
            g: {
                band: {k: str(HR.RATES[tier][g][band][k]) for k in ("main", "adult_dep", "child_dep")}
                for band in HR.RATES[tier][g]
            }
            for g in HR.RATES[tier]
        }
        for tier in HR.RATES
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def test_vat_rate_has_not_drifted():
    assert str(HR.VAT_RATE) == EXPECTED_VAT_RATE, (
        "health_rates.VAT_RATE changed — the broker PWA bundles it. Re-export + "
        "redeploy health-broker-pwa, then bump EXPECTED_VAT_RATE."
    )


def test_health_office_rates_have_not_drifted():
    assert _fingerprint() == EXPECTED_FINGERPRINT, (
        "Health office rates changed. The broker PWA (health-broker-pwa) bundles a "
        "COPY — re-run its scripts/export-rate-table.py, `npm test`, redeploy, then "
        "bump EXPECTED_FINGERPRINT here. Until then the broker app quotes the OLD rates."
    )
