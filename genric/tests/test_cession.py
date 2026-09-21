"""WHY: the July 2026 worked example is the acceptance test for the whole pack.

Finance signed off a pack whose every figure is printed to the cent. If Omni
produces a different cent, Omni is wrong — and the build prompt is explicit
that a difference is REPORTED, never adjusted to match. These tests pin the
signed figures so a later "tidy-up" of the rounding or the step order cannot
move the invoice total without a red test.

Source: Alpha_Direct_GENRIC_Reporting_Build_Prompt.docx, "The cession runs in
this exact order" and "Acceptance test".
"""
from decimal import Decimal

from django.test import SimpleTestCase

from genric import constants as K
from genric.cession import compute_cession
from genric.money import q2


class CedingCommissionRateTests(SimpleTestCase):
    """The one number the whole invoice turns on."""

    def test_rate_is_the_signed_treaty_21_5_percent_not_the_mou_10(self):
        # The older MOU said 10% — the signed treaty wins.
        self.assertEqual(K.CEDING_COMMISSION_RATE, Decimal('0.215'))
        self.assertNotEqual(K.CEDING_COMMISSION_RATE, Decimal('0.10'))

    def test_ten_percent_would_produce_a_visibly_different_invoice(self):
        """Proves the rate is doing work, not decoration.

        At 10% the July invoice would be R6,484.93, not R5,656.30 — R828.63
        more billed to GENRIC every month.
        """
        at_treaty = compute_cession(Decimal('9207.00'))
        ceded = at_treaty.ceded
        at_mou = q2(ceded - q2(ceded * Decimal('0.10')))
        self.assertEqual(at_treaty.net_reinsurance_premium_due, Decimal('5656.30'))
        self.assertEqual(at_mou, Decimal('6484.93'))

    def test_treaty_shares_must_sum_to_one(self):
        self.assertEqual(K.QUOTA_SHARE_CEDED + K.RETENTION, Decimal('1'))


class JulyWorkedExampleTests(SimpleTestCase):
    """Every step of the signed July 2026 cession, to the thebe."""

    def setUp(self):
        self.c = compute_cession(Decimal('9207.00'))

    def test_step_1_gwp_excl_vat(self):
        self.assertEqual(self.c.gwp_excl_vat, Decimal('8006.09'))

    def test_step_2_collection_charges_are_zero_unless_advised(self):
        self.assertEqual(self.c.collection_charges, Decimal('0.00'))

    def test_step_3_net_premium_base(self):
        self.assertEqual(self.c.net_premium_base, Decimal('8006.09'))

    def test_step_4_ceded_at_ninety_percent(self):
        self.assertEqual(self.c.ceded, Decimal('7205.48'))

    def test_step_5_ceding_commission_at_21_5_percent(self):
        self.assertEqual(self.c.ceding_commission, Decimal('1549.18'))

    def test_step_6_net_reinsurance_premium_due_is_the_invoice_total(self):
        self.assertEqual(self.c.net_reinsurance_premium_due, Decimal('5656.30'))
        self.assertEqual(self.c.invoice_total, Decimal('5656.30'))

    def test_the_printed_steps_add_up_to_the_printed_total(self):
        """A human re-keying the invoice adds the printed steps, not the
        full-precision ones. Step 4 + step 5 must equal step 6 exactly."""
        step4 = next(s for s in self.c.steps if s.number == 4).amount
        step5 = next(s for s in self.c.steps if s.number == 5).amount
        step6 = next(s for s in self.c.steps if s.number == 6).amount
        self.assertEqual(q2(step4 + step5), step6)

    def test_retained_plus_ceded_is_the_whole_base(self):
        self.assertEqual(q2(self.c.ceded + self.c.retained), self.c.net_premium_base)


class RoundingTests(SimpleTestCase):
    """Rounding is a decision, not a language default."""

    def test_half_up_not_bankers_rounding(self):
        """0.005 must go UP. Python's round() sends it DOWN to an even cent.

        A GWP of R0.0115 incl VAT is contrived; the point is the boundary. If
        this ever goes red, somebody reached for round() and the invoice is now
        a cent light on every halfway case.
        """
        self.assertEqual(q2(Decimal('2.005')), Decimal('2.01'))
        self.assertEqual(q2(Decimal('2.015')), Decimal('2.02'))
        self.assertEqual(q2(Decimal('-2.005')), Decimal('-2.01'))
        # What banker's rounding would have said:
        self.assertNotEqual(q2(Decimal('2.005')), Decimal('2.00'))

    def test_vat_is_stripped_by_division_not_by_a_rounded_reciprocal(self):
        """R1,000,000 incl VAT. Dividing gives 869,565.22; multiplying by a
        rounded 0.8696 gives 869,600.00 — R34.78 of invented premium."""
        c = compute_cession(Decimal('1000000.00'))
        self.assertEqual(c.gwp_excl_vat, Decimal('869565.22'))


class OtherMonthTests(SimpleTestCase):
    """"Then run it for a different month to prove it is not hard-coded."""

    def test_a_different_gwp_produces_a_different_invoice(self):
        c = compute_cession(Decimal('12000.00'))
        self.assertEqual(c.gwp_excl_vat, Decimal('10434.78'))
        self.assertEqual(c.ceded, Decimal('9391.30'))
        self.assertEqual(c.ceding_commission, Decimal('2019.13'))
        self.assertEqual(c.net_reinsurance_premium_due, Decimal('7372.17'))

    def test_a_nil_month_produces_a_nil_invoice_not_an_error(self):
        c = compute_cession(Decimal('0.00'))
        self.assertEqual(c.net_reinsurance_premium_due, Decimal('0.00'))

    def test_collection_charges_reduce_the_base_when_advised(self):
        c = compute_cession(Decimal('9207.00'), Decimal('106.09'))
        self.assertEqual(c.net_premium_base, Decimal('7900.00'))
        self.assertEqual(c.ceded, Decimal('7110.00'))
        self.assertEqual(c.ceding_commission, Decimal('1528.65'))
        self.assertEqual(c.net_reinsurance_premium_due, Decimal('5581.35'))
