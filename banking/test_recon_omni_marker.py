"""banking/test_recon_omni_marker.py — recon must see through the (O) marker.

The FNB "Our reference" (endToEndId) now carries the Omni-origin marker
" (O)" (CFO 2026-08-22). The bank echoes it into the statement line's reference,
so the exact-reference match in ReconciliationEngine must strip a trailing
marker or it degrades a 95%-confidence match to an amount-only 60%.

Stub-based (no DB): _match_line only reads attributes off the line and payment.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase


class _Line:
    def __init__(self, reference, amount, transaction_date):
        self.reference = reference
        self.amount = amount
        self.transaction_date = transaction_date


class _Pay:
    """Minimal stand-in. No wht_record attribute, so _effective_amount falls back
    to amount_bwp (the except branch) — exactly what a plain vendor payment does."""
    def __init__(self, reference, amount_bwp, payment_date):
        from payments.models import Payment as P
        self.reference = reference
        self.amount_bwp = amount_bwp
        self.payment_type = P.PaymentType.SENT
        self.payment_date = payment_date


class ReconOmniMarkerTests(SimpleTestCase):

    def _engine(self):
        from banking.services import ReconciliationEngine
        return ReconciliationEngine(None)

    def test_exact_reference_still_matches_through_the_marker(self):
        eng = self._engine()
        # Bank echoed our marked endToEndId; our internal reference is unmarked.
        line = _Line('INV-4471 (O)', Decimal('-100.00'), date(2026, 8, 22))
        # Date 10 days off so Rule 2 (amount + <=3 days) cannot rescue it — only
        # the exact-reference Rule 1 (or the weaker amount-only Rule 3) can fire.
        pay = _Pay('INV-4471', Decimal('100.00'), date(2026, 8, 1))
        by_ref = {'INV-4471': [pay]}

        result = eng._match_line(line, [pay], by_ref)

        self.assertIsNotNone(result)
        matched, confidence = result
        self.assertIs(matched, pay)
        # Must be the 95% exact-reference match, NOT the 60% amount-only fallback.
        self.assertEqual(confidence, eng.CONFIDENCE_RULE1)
