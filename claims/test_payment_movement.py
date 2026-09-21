"""
claims/test_payment_movement.py — B6 Claims Payment Movement Report.

The report exists because of ONE mistake: VAT being de-grossed off payments
that never carried VAT. A 12-row sample in the source document had 3,984.39 of
37,061.52 — about 11% — wrongly de-grossed. So the first test here is a mixed
batch carrying one row of EACH basis, and it proves that exactly one of them is
de-grossed.

A batch where every row shares a basis proves nothing: it passes whether the
rule reads the basis or ignores it. The mixed batch is the case that
discriminates.

Pure-Python engine, no ORM — plain unittest, no database needed.

Run:  python manage.py test claims.test_payment_movement
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from banking.line_references import PaymentBasis, extract_line_references
from claims.payment_movement_views import ClaimsPaymentMovementView
from claims.reconciliation.engine import money
from claims.payment_movement import (
    INDIVIDUAL_CLAIMANT,
    SUPPLIER_INVOICE,
    ClaimPaymentRow,
    build_payment_movement,
)

# Botswana VAT. Passed in explicitly everywhere — the production caller reads
# settings.RC_VAT_RATE and there is no second constant anywhere.
RATE = Decimal('0.14')


def row(basis, amount, *, claim='G2026004512', invoice='', payee='A Payee'):
    return ClaimPaymentRow(
        transaction_date=date(2026, 7, 15),
        payee=payee,
        claim_reference=claim,
        invoice_reference=invoice,
        payment_basis=basis,
        amount_gross=Decimal(amount),
    )


class MixedBasisVatRule(unittest.TestCase):
    """THE test. One row of each basis, one batch, one assertion set."""

    def setUp(self):
        # 1,140.00 chosen so the INVOICE answer is exact and unarguable:
        # 1,140.00 / 1.14 = 1,000.00 excl, 140.00 VAT.
        self.payments = [
            row(PaymentBasis.INVOICE, '1140.00', invoice='INV45678',
                payee='Motovac'),
            row(PaymentBasis.AOL, '1140.00', payee='T Modise'),
            row(PaymentBasis.FOR, '1140.00', payee='K Baleseng'),
            row(PaymentBasis.CIL, '1140.00', payee='N Phiri'),
        ]
        self.report = build_payment_movement(self.payments, RATE)

    def test_only_the_invoice_row_is_degrossed(self):
        flags = {r.source.payment_basis: r.was_degrossed for r in self.report.rows}
        self.assertEqual(flags, {
            PaymentBasis.INVOICE: True,
            PaymentBasis.AOL: False,
            PaymentBasis.FOR: False,
            PaymentBasis.CIL: False,
        })
        self.assertEqual(self.report.degrossed_count, 1)
        self.assertEqual(self.report.left_gross_count, 3)

    def test_the_invoice_row_degrosses_to_the_right_figures(self):
        inv = next(r for r in self.report.rows
                   if r.source.payment_basis == PaymentBasis.INVOICE)
        self.assertEqual(inv.amount_gross, Decimal('1140.00'))
        self.assertEqual(inv.amount_excl_vat, Decimal('1000.00'))
        self.assertEqual(inv.vat_amount, Decimal('140.00'))

    def test_aol_for_and_cil_come_through_untouched(self):
        for basis in (PaymentBasis.AOL, PaymentBasis.FOR, PaymentBasis.CIL):
            with self.subTest(basis=basis):
                r = next(x for x in self.report.rows
                         if x.source.payment_basis == basis)
                self.assertEqual(r.amount_excl_vat, Decimal('1140.00'),
                                 f'{basis} was de-grossed and must not be')
                self.assertEqual(r.vat_amount, Decimal('0.00'))

    def test_total_vat_is_only_the_invoice_rows_vat(self):
        # 4 x 1,140.00 = 4,560.00 gross. If AOL/FOR/CIL were wrongly
        # de-grossed the VAT total would be 560.00, not 140.00 — which is the
        # shape of the real 11% over-claim.
        self.assertEqual(self.report.total.amount_gross, Decimal('4560.00'))
        self.assertEqual(self.report.total.vat_amount, Decimal('140.00'))
        self.assertEqual(self.report.total.amount_excl_vat, Decimal('4420.00'))

    def test_every_basis_gets_its_own_analysis_line(self):
        keys = [b.key for b in self.report.by_type]
        self.assertEqual(keys, [PaymentBasis.INVOICE, PaymentBasis.AOL,
                                PaymentBasis.FOR, PaymentBasis.CIL])
        self.assertTrue(all(b.count == 1 for b in self.report.by_type))


class UnknownBasis(unittest.TestCase):
    def test_an_unreadable_basis_is_never_degrossed(self):
        """Doubt resolves to leaving the money gross. The error being
        corrected is over-de-grossing, so UNKNOWN behaves like AOL."""
        rep = build_payment_movement([row(PaymentBasis.UNKNOWN, '1140.00')], RATE)
        self.assertFalse(rep.rows[0].was_degrossed)
        self.assertEqual(rep.rows[0].vat_amount, Decimal('0.00'))
        self.assertEqual(rep.rows[0].amount_excl_vat, Decimal('1140.00'))


class HalfUpRounding(unittest.TestCase):
    """Rounding is a tax decision, never a language default.

    At 14% no two-decimal gross lands exactly on a half-cent when divided by
    1.14 (it would need the excl-VAT figure to be a multiple of 0.005 whose
    gross is still 2dp, and 114 x that is never a whole cent). So the tie is
    proved on the shared quantiser `money()` itself — the single function every
    figure in this report passes through — and the report is then shown to use
    it.
    """

    def test_the_shared_quantiser_rounds_half_up(self):
        self.assertEqual(money(Decimal('2.125')), Decimal('2.13'))
        self.assertEqual(money(Decimal('0.005')), Decimal('0.01'))

    def test_pythons_own_round_would_have_given_the_other_answer(self):
        """Why money() exists at all: round() does banker's rounding."""
        # 2.125 is exactly representable in binary, so this is a true tie and
        # round() resolves it to even — 2.12, one cent under the tax answer.
        # (0.005 is NOT exactly representable and rounds to 0.01 by accident,
        # which is why it makes a useless test.)
        self.assertEqual(round(2.125, 2), 2.12)

    def test_degrossing_rounds_up_rather_than_truncating(self):
        """100.00 / 1.14 = 87.7192... -> 87.72 rounded, 87.71 truncated."""
        rep = build_payment_movement(
            [row(PaymentBasis.INVOICE, '100.00', invoice='INV1')], RATE)
        self.assertEqual(rep.rows[0].amount_excl_vat, Decimal('87.72'))
        self.assertEqual(rep.rows[0].vat_amount, Decimal('12.28'))
        self.assertEqual(
            rep.rows[0].amount_excl_vat + rep.rows[0].vat_amount,
            rep.rows[0].amount_gross,
            'excl + VAT must add back to the gross, to the cent',
        )


class SupplierVersusClaimantSplit(unittest.TestCase):
    def setUp(self):
        self.report = build_payment_movement([
            row(PaymentBasis.INVOICE, '1140.00', invoice='INV45678',
                payee='Motovac'),
            row(PaymentBasis.INVOICE, '2280.00', invoice='INV99',
                payee='Autoscreen'),
            row(PaymentBasis.AOL, '500.00', payee='T Modise'),
            row(PaymentBasis.CIL, '300.00', payee='N Phiri'),
        ], RATE)

    def test_the_two_sides_are_split_on_the_invoice_number(self):
        got = {b.key: (b.count, b.amount_gross) for b in self.report.by_settlement}
        self.assertEqual(got[SUPPLIER_INVOICE], (2, Decimal('3420.00')))
        self.assertEqual(got[INDIVIDUAL_CLAIMANT], (2, Decimal('800.00')))

    def test_the_split_sums_back_to_the_total(self):
        self.assertEqual(
            sum(b.amount_gross for b in self.report.by_settlement),
            self.report.total.amount_gross,
        )


class EmptyPeriod(unittest.TestCase):
    def test_no_payments_is_a_report_not_a_crash(self):
        rep = build_payment_movement([], RATE)
        self.assertEqual(rep.total.count, 0)
        self.assertEqual(rep.total.amount_gross, Decimal('0.00'))
        self.assertEqual(rep.by_type, [])
        self.assertEqual(rep.by_settlement, [])

    def test_the_rate_is_never_invented(self):
        with self.assertRaises(ValueError):
            build_payment_movement([], None)


class Cr001FeedsTheRule(unittest.TestCase):
    """The join: what CR-001 reads off a bank line drives which rows de-gross.

    Tested together because that join is where the report can silently go
    wrong — a parser that labels an AOL narration as INVOICE would re-create
    the exact over-claim, with every unit test above still green.
    """

    def test_house_narrations_produce_the_right_bases(self):
        cases = [
            ('G2026004512 AOL', PaymentBasis.AOL, ''),
            ('G2026004512 CIL', PaymentBasis.CIL, ''),
            ('G2026004512 FOR', PaymentBasis.FOR, ''),
            ('MOTOVAC INV45678', PaymentBasis.INVOICE, '45678'),
            ('G2026004567-4546', PaymentBasis.INVOICE, '4546'),
        ]
        for text, basis, invoice in cases:
            with self.subTest(text=text):
                refs = extract_line_references(text, '')
                self.assertEqual(refs.payment_basis, basis)
                self.assertEqual(refs.invoice_reference, invoice)

    def test_the_word_for_in_prose_is_not_a_basis_code(self):
        """'for' is the commonest word in narration text. Matching it anywhere
        would label ordinary prose as a payment basis."""
        refs = extract_line_references('PAYMENT FOR REPAIRS G2026004512', '')
        self.assertNotEqual(refs.payment_basis, PaymentBasis.FOR)

    def test_an_aol_line_is_never_read_as_a_supplier_settlement(self):
        """An AOL payment goes to a person, not against an invoice. If a
        numeric token leaked through as an invoice number the row would land
        on the wrong side of the split AND become de-grossable."""
        refs = extract_line_references('ALPHA DIRECT G2026004512 AOL 20240956', '')
        self.assertEqual(refs.payment_basis, PaymentBasis.AOL)
        self.assertEqual(refs.invoice_reference, '')
        self.assertFalse(refs.settled_against_invoice)

    def test_nothing_readable_stays_unknown(self):
        refs = extract_line_references('MONTHLY SERVICE FEE', '')
        self.assertEqual(refs.payment_basis, PaymentBasis.UNKNOWN)
        self.assertEqual(refs.claim_reference, '')

    def test_the_parsed_bases_drive_the_report_end_to_end(self):
        narrations = ['G2026004512 AOL', 'G2026004513 CIL', 'G2026004514 FOR',
                      'MOTOVAC INV45678']
        payments = []
        for text in narrations:
            refs = extract_line_references(text, '')
            payments.append(ClaimPaymentRow(
                transaction_date=date(2026, 7, 15),
                payee='—',
                claim_reference=refs.claim_reference,
                invoice_reference=refs.invoice_reference,
                payment_basis=refs.payment_basis,
                amount_gross=Decimal('1140.00'),
            ))
        rep = build_payment_movement(payments, RATE)
        self.assertEqual(rep.degrossed_count, 1)
        self.assertEqual(rep.total.vat_amount, Decimal('140.00'))


class TheAccessGate(TestCase):
    """Finance only — and PINNED, not merely written.

    The rows put payee names next to claim amounts. The sidebar entry is not
    the control; permission_classes on the view is. Fable 5.1 pointed out that
    the gate was correct and nothing held it in place, so deleting the line
    would have gone green. This is the test that goes red if someone does.
    """

    @classmethod
    def setUpTestData(cls):
        cls.finance = User.objects.create_superuser(
            'cpm_fin', 'cpm_fin@example.com', 'x')
        cls.operational = User.objects.create_user(
            'cpm_ops', 'cpm_ops@example.com', 'x')

    def _get(self, user, **params):
        req = APIRequestFactory().get(
            '/api/v1/reports/claims-payment-movement/', params)
        force_authenticate(req, user=user)
        return ClaimsPaymentMovementView.as_view()(req)

    def test_an_ordinary_staff_member_is_refused(self):
        self.assertEqual(self._get(self.operational).status_code, 403)

    def test_nobody_at_all_is_refused(self):
        req = APIRequestFactory().get('/api/v1/reports/claims-payment-movement/')
        self.assertIn(ClaimsPaymentMovementView.as_view()(req).status_code,
                      (401, 403))

    def test_finance_gets_the_report(self):
        resp = self._get(self.finance, **{'from': '2026-07-01', 'to': '2026-07-31'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['vat_rate'], str(settings.RC_VAT_RATE))

    def test_a_nonsense_bank_account_is_a_400_not_a_500(self):
        resp = self._get(self.finance, bank_account='not-a-uuid')
        self.assertEqual(resp.status_code, 400)


if __name__ == '__main__':
    unittest.main()
