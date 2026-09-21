"""Tests for the broker commission engine (C6) and summary (C7).

The maths tests are pure — no database, no Graphite — so they can be re-run
against Finance's workbook the moment the August rows arrive.

Every test here was red-proved: each one fails if the behaviour it names is
removed. The refusals matter most — a screen that quietly prints P0.00 when it
does not know the answer is the failure this whole module was shaped around.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase

from commissions import broker_summary
from commissions.commission_calc import (
    Rates, compute, growth_pct, money, strip_vat_and_admin)
from commissions.models import Broker, BrokerAlias, BrokerCommissionRate


SETTLED = Rates(motor_pct=Decimal('12.5'), non_motor_pct=Decimal('20'),
                vat_pct=Decimal('14'), admin_pct=Decimal('8'),
                wht_pct=Decimal('10'))


class MoneyRoundingTests(SimpleTestCase):
    def test_half_up_not_bankers(self):
        # Python's default rounding would give 0.12 here (round-half-even).
        # Rounding is a tax decision: BURS is half UP.
        self.assertEqual(money(Decimal('0.125')), Decimal('0.13'))
        self.assertEqual(money(Decimal('0.135')), Decimal('0.14'))

    def test_none_is_zero_not_a_crash(self):
        self.assertEqual(money(None), Decimal('0.00'))


class StripVatAndAdminTests(SimpleTestCase):
    def test_removes_both_vat_and_admin(self):
        # 1000 gross -> /1.14 -> 877.192982 -> /1.08 -> 812.216
        got = strip_vat_and_admin(Decimal('1000'), SETTLED)
        self.assertEqual(money(got), Decimal('812.22'))

    def test_admin_is_not_skipped(self):
        """Dividing by VAT alone over-pays every broker by ~8%.

        Red-proves the second division: with it removed the answer is 877.19.
        """
        self.assertNotEqual(money(strip_vat_and_admin(Decimal('1000'), SETTLED)),
                            Decimal('877.19'))


class ComputeTests(SimpleTestCase):
    def test_all_non_motor_worked_example(self):
        c = compute(motor_collected=0, non_motor_collected=Decimal('1000'),
                    rates=SETTLED, withholding=True)
        self.assertEqual(c.net_premium, Decimal('812.22'))
        self.assertEqual(c.non_motor_net, Decimal('812.22'))
        self.assertEqual(c.motor_net, Decimal('0.00'))
        # 812.216... * 20% = 162.44
        self.assertEqual(c.commission_excl_vat, Decimal('162.44'))
        self.assertEqual(c.wht, Decimal('16.24'))
        self.assertEqual(c.vat, Decimal('22.74'))
        self.assertEqual(c.current_payable, Decimal('168.94'))

    def test_all_motor_uses_the_motor_rate(self):
        c = compute(motor_collected=Decimal('1000'), non_motor_collected=0, rates=SETTLED)
        # 812.216... * 12.5% = 101.53
        self.assertEqual(c.commission_excl_vat, Decimal('101.53'))
        self.assertEqual(c.motor_net, Decimal('812.22'))
        self.assertEqual(c.non_motor_net, Decimal('0.00'))

    def test_a_motor_pula_and_a_non_motor_pula_earn_different_commission(self):
        """The whole reason the split is carried separately: 1000 of motor and
        1000 of non-motor cannot be netted to 2000 and rated once."""
        mixed = compute(Decimal('1000'), Decimal('1000'), SETTLED)
        netted_wrong = compute(0, Decimal('2000'), SETTLED)
        self.assertNotEqual(mixed.commission_excl_vat, netted_wrong.commission_excl_vat)
        # 812.22*12.5% + 812.22*20% = 101.53 + 162.44 = 263.97
        self.assertEqual(mixed.commission_excl_vat, Decimal('263.97'))

    def test_payable_reconciles_to_the_cent(self):
        """payable must equal commission - WHT + VAT exactly, at any amount.

        Red-proves rounding the commission BEFORE deriving WHT and VAT: derive
        them from the unrounded base instead and this drifts a thebe.
        """
        for m in ('1000', '12345.67', '0.01', '99999.99'):
            for nm in ('0', '500', '47955.13'):
                c = compute(Decimal(m), Decimal(nm), SETTLED)
                self.assertEqual(
                    c.current_payable,
                    c.commission_excl_vat - c.wht + c.vat,
                    msg=f'motor={m} non_motor={nm}')

    def test_exempt_broker_pays_no_withholding(self):
        c = compute(0, Decimal('1000'), SETTLED, withholding=False)
        self.assertEqual(c.wht, Decimal('0.00'))
        self.assertFalse(c.withholding_applied)
        self.assertEqual(c.current_payable, c.commission_excl_vat + c.vat)

    def test_a_negative_collected_is_refused(self):
        with self.assertRaises(ValueError):
            compute(Decimal('-1'), 0, SETTLED)

    def test_zero_collected_is_zero_commission_not_a_crash(self):
        c = compute(0, 0, SETTLED)
        self.assertEqual(c.current_payable, Decimal('0.00'))


class GrowthTests(SimpleTestCase):
    def test_no_previous_month_is_none_not_zero(self):
        """None, not 0: 0% reads as 'flat' when it means 'no history'."""
        self.assertIsNone(growth_pct(100, 0))

    def test_ordinary_growth(self):
        self.assertEqual(growth_pct(150, 100), 50.0)
        self.assertEqual(growth_pct(50, 100), -50.0)


class RateInForceTests(TestCase):
    def test_picks_the_row_governing_the_period_not_the_newest(self):
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1),
                                            motor_pct=Decimal('10'))
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 9, 1),
                                            motor_pct=Decimal('12.5'))
        old = BrokerCommissionRate.in_force_on(_dt.date(2026, 8, 1))
        self.assertEqual(old.motor_pct, Decimal('10.000'))
        new = BrokerCommissionRate.in_force_on(_dt.date(2026, 9, 1))
        self.assertEqual(new.motor_pct, Decimal('12.500'))

    def test_nothing_configured_returns_none_not_a_default(self):
        self.assertIsNone(BrokerCommissionRate.in_force_on(_dt.date(2026, 9, 1)))


class MonthBoundsTests(SimpleTestCase):
    def test_end_is_the_first_of_the_next_month_exclusive(self):
        self.assertEqual(broker_summary.month_bounds('2026-09'),
                         (_dt.date(2026, 9, 1), _dt.date(2026, 10, 1)))

    def test_december_rolls_the_year(self):
        self.assertEqual(broker_summary.month_bounds('2026-12'),
                         (_dt.date(2026, 12, 1), _dt.date(2027, 1, 1)))

    def test_previous_period_rolls_back_over_january(self):
        self.assertEqual(broker_summary.previous_period('2026-01'), '2025-12')
        self.assertEqual(broker_summary.previous_period('2026-09'), '2026-08')


class SummaryTests(TestCase):
    def setUp(self):
        self.broker = Broker.objects.create(name='Redhill Risk Solutions')
        BrokerAlias.objects.create(
            broker=self.broker,
            graphite_agency_name='Hilrange Enterprises (Pty) Ltd T/a Redhill Risk Solutions')

    def _patch(self, collected, health):
        return mock.patch.multiple(
            'commissions.broker_summary.graphite_feed',
            collected_by_agency=mock.Mock(return_value=collected),
            outcome_feed_health=mock.Mock(return_value=health))

    REPORTING = {'reporting': True, 'resolved': 10, 'awaiting': 1,
                 'window_rows': 11, 'reason': ''}

    def _agency(self, motor, non_motor, count=1):
        return {'motor': motor, 'non_motor': non_motor,
                'amount': motor + non_motor, 'count': count}

    def test_no_rates_configured_blocks_every_commission_figure(self):
        collected = {'Hilrange Enterprises (Pty) Ltd T/a Redhill Risk Solutions':
                     self._agency(0.0, 1000.0, 3)}
        with self._patch(collected, self.REPORTING):
            out = broker_summary.build('2026-09')
        self.assertFalse(out['rates_configured'])
        row = out['rows'][0]
        self.assertFalse(row['commission_available'])
        self.assertNotIn('current_payable', row)
        self.assertIn('rates', row['blocked_reason'].lower())
        # The premium collected is still shown — that part IS known.
        self.assertEqual(row['collected_gross'], 1000.0)

    def test_motor_split_flows_through_to_the_figures(self):
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1))
        self.broker.motor_share_note = ''  # noqa - just documenting no config needed
        collected = {'Hilrange Enterprises (Pty) Ltd T/a Redhill Risk Solutions':
                     self._agency(1000.0, 1000.0, 4)}
        with self._patch(collected, self.REPORTING):
            out = broker_summary.build('2026-09')
        row = out['rows'][0]
        self.assertTrue(row['commission_available'])
        self.assertEqual(row['motor_collected'], 1000.0)
        self.assertEqual(row['non_motor_collected'], 1000.0)
        # 812.22*12.5% + 812.22*20% = 263.97
        self.assertEqual(row['commission_excl_vat'], 263.97)

    def test_outcome_feed_not_reporting_flags_the_figures_incomplete(self):
        """PROVEN live 17-Sep-2026: September DOM/COM had 679 debits raised with
        no result against 18 collected. Rendering that as the month's
        collections would understate every broker's commission."""
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1))
        dead = {'reporting': False, 'resolved': 18, 'awaiting': 679,
                'window_rows': 697, 'reason': '679 of 697 debits are still awaiting a result'}
        with self._patch({}, dead):
            out = broker_summary.build('2026-09')
        self.assertFalse(out['figures_complete'])
        self.assertIn('awaiting', out['feed']['reason'])

    def test_aliases_roll_up_onto_one_broker(self):
        """Redhill is TWO agency rows in Graphite. Paying per row splits one
        broker's commission in half."""
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1))
        BrokerAlias.objects.create(
            broker=self.broker,
            graphite_agency_name='Hildrage Enterprises (Pty) Ltd T/a Redhill Risk Solutions')
        collected = {
            'Hilrange Enterprises (Pty) Ltd T/a Redhill Risk Solutions': self._agency(0.0, 1000.0, 3),
            'Hildrage Enterprises (Pty) Ltd T/a Redhill Risk Solutions': self._agency(0.0, 500.0, 2),
        }
        with self._patch(collected, self.REPORTING):
            out = broker_summary.build('2026-09')
        rows = [r for r in out['rows'] if r['broker'] == 'Redhill Risk Solutions']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['collected_gross'], 1500.0)
        self.assertEqual(rows[0]['collected_count'], 5)

    def test_alpha_directs_own_book_earns_nobody_commission(self):
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1))
        collected = {'Alpha Direct Insurance Co. (Pty) Ltd': self._agency(50000.0, 49999.0, 50)}
        with self._patch(collected, self.REPORTING):
            out = broker_summary.build('2026-09')
        self.assertEqual(out['totals']['collected_gross'], 0.0)
        self.assertNotIn('Alpha Direct Insurance Co. (Pty) Ltd', out['unmapped_agencies'])

    def test_an_unknown_agency_is_surfaced_not_silently_dropped(self):
        """Money collected under an agency no broker claims is a real gap —
        someone is owed commission and the register does not know who."""
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1))
        collected = {'Brand New Brokers (Pty) Ltd': self._agency(0.0, 4000.0, 9)}
        with self._patch(collected, self.REPORTING):
            out = broker_summary.build('2026-09')
        self.assertIn('Brand New Brokers (Pty) Ltd', out['unmapped_agencies'])

    def test_the_response_always_says_it_is_a_preview(self):
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1))
        with self._patch({}, self.REPORTING):
            out = broker_summary.build('2026-09')
        self.assertTrue(out['preview'])
        self.assertFalse(out['signed_off'])
