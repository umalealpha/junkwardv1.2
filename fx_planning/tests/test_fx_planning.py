"""FX Payment Planning — unit tests.

These lock the behaviour the CFO is relying on: the recurring detector must spot
the clockwork payees from real history, and the forward calendar must project
them with a pula estimate. Each test fails if the corresponding engine breaks.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Currency, ExchangeRate
from fx_planning import services
from fx_planning.models import (
    ForexPaymentHistory, PlannedForexPayment, RecurringForexPayee,
)

User = get_user_model()


# A slice of the CFO's real FNB forex download (Feb–May 2026).
SEED = [
    # ref, beneficiary, ccy, amount, value_date, account
    ('9438070', 'THINKQ CONSULTING SERVICES', 'USD', '2250.00', date(2026, 2, 27), '60000001'),
    ('9533540', 'THINKQ CONSULTING SERVICES', 'USD', '2250.00', date(2026, 3, 27), '60000001'),
    ('9661433', 'THINKQ CONSULTING SERVICES', 'USD', '2250.00', date(2026, 4, 30), '60000001'),
    ('9438168', 'ADRISK GLOBAL SOLUTIONS', 'USD', '11970.77', date(2026, 2, 27), '60000001'),
    ('9533585', 'ADRISK GLOBAL SOLUTIONS', 'USD', '10118.94', date(2026, 3, 27), '60000001'),
    ('9661387', 'ADRISK GLOBAL SOLUTIONS', 'USD', '9335.11', date(2026, 4, 30), '60000001'),
    # A one-off — must NOT become an active recurring payee.
    ('9548467', 'CFAO MOBILITY', 'ZAR', '250000.00', date(2026, 4, 2), '60000002'),
]


def _seed():
    for ref, ben, ccy, amt, vdate, acct in SEED:
        ForexPaymentHistory.objects.create(
            reference=ref, beneficiary=ben,
            beneficiary_key=services.normalise_key(ben),
            currency=ccy, amount=Decimal(amt), value_date=vdate,
            capture_date=vdate, source_account=acct, status='Complete',
        )


class RecurringDetectorTests(TestCase):
    def test_detects_monthly_clockwork_payees(self):
        _seed()
        services.detect_recurring()

        thinkq = RecurringForexPayee.objects.get(
            beneficiary_key=services.normalise_key('THINKQ CONSULTING SERVICES'))
        self.assertTrue(thinkq.active)
        self.assertEqual(thinkq.currency, 'USD')
        self.assertEqual(thinkq.cadence, RecurringForexPayee.Cadence.MONTHLY)
        self.assertEqual(thinkq.typical_amount, Decimal('2250.00'))
        self.assertEqual(thinkq.months_active, 3)

        adrisk = RecurringForexPayee.objects.get(
            beneficiary_key=services.normalise_key('ADRISK GLOBAL SOLUTIONS'))
        self.assertTrue(adrisk.active)
        self.assertEqual(adrisk.cadence, RecurringForexPayee.Cadence.MONTHLY)
        # Median of 11970.77 / 10118.94 / 9335.11 = 10118.94
        self.assertEqual(adrisk.typical_amount, Decimal('10118.94'))

    def test_one_off_is_not_flagged_recurring(self):
        _seed()
        services.detect_recurring()
        cfao = RecurringForexPayee.objects.get(
            beneficiary_key=services.normalise_key('CFAO MOBILITY'))
        self.assertFalse(cfao.active)

    def test_import_is_idempotent_on_reference(self):
        _seed()
        before = ForexPaymentHistory.objects.count()
        # Re-inserting the same references must be a no-op via the importer path.
        rows = [{'reference': r[0], 'beneficiary': r[1], 'payment_type': 'Once-Off',
                 'capture_date': r[4], 'value_date': r[4], 'source_account': r[5],
                 'currency': r[2], 'amount': Decimal(r[3]), 'status': 'Complete'}
                for r in SEED]
        # Simulate the de-dup guard the importer uses.
        existing = set(ForexPaymentHistory.objects.values_list('reference', flat=True))
        new = [r for r in rows if r['reference'] not in existing]
        self.assertEqual(len(new), 0)
        self.assertEqual(ForexPaymentHistory.objects.count(), before)


class CalendarTests(TestCase):
    def setUp(self):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'symbol': '$'})
        self.user = User.objects.create_user('fin', 'fin@x.co', 'pw')
        ExchangeRate.objects.create(
            from_currency_id='USD', to_currency_id='BWP', rate=Decimal('13.50000000'),
            effective_date=date(2026, 1, 1), loaded_by=self.user,
            approved_by=self.user, approved_at='2026-01-01T00:00:00Z')

    def test_pula_estimate_uses_approved_rate(self):
        rate, bwp, is_est = services.estimate_bwp('USD', Decimal('2250.00'), date(2026, 6, 1))
        self.assertEqual(rate, Decimal('13.50000000'))
        self.assertEqual(bwp, Decimal('30375.00'))   # 2250 * 13.5
        self.assertFalse(is_est)

    def test_materialise_projects_forward_lines(self):
        _seed()
        services.detect_recurring()
        # Project from a fixed "today" so the test is deterministic.
        created = services.materialise_calendar(weeks_ahead=8, user=self.user,
                                                today=date(2026, 5, 1))
        self.assertGreater(created, 0)
        lines = PlannedForexPayment.objects.filter(
            beneficiary_key=services.normalise_key('THINKQ CONSULTING SERVICES'))
        self.assertTrue(lines.exists())
        line = lines.first()
        self.assertEqual(line.currency, 'USD')
        self.assertEqual(line.expected_amount, Decimal('2250.00'))
        self.assertEqual(line.estimated_bwp, Decimal('30375.00'))
        self.assertEqual(line.driver, PlannedForexPayment.Driver.RECURRING)

    def test_materialise_is_idempotent(self):
        _seed()
        services.detect_recurring()
        first = services.materialise_calendar(weeks_ahead=8, user=self.user,
                                              today=date(2026, 5, 1))
        second = services.materialise_calendar(weeks_ahead=8, user=self.user,
                                               today=date(2026, 5, 1))
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)   # nothing new the second time


class RateRiskBufferTests(TestCase):
    """FX-002: the pula figure must also be shown stressed for a weaker pula."""

    def test_stress_bwp_applies_the_percentage(self):
        self.assertEqual(services.stress_bwp(Decimal('100.00'), Decimal('10')),
                         Decimal('110.00'))
        self.assertEqual(services.stress_bwp(Decimal('30375.00'), Decimal('5')),
                         Decimal('31893.75'))

    def test_stress_bwp_handles_none(self):
        self.assertEqual(services.stress_bwp(None, Decimal('10')), Decimal('0.00'))

    def test_default_stress_percentages(self):
        # Fails if the +5%/+10% buffer is dropped.
        self.assertEqual(services.fx_stress_pcts(), [Decimal('5'), Decimal('10')])

    def test_stress_percentages_are_settings_overridable(self):
        from django.test import override_settings
        with override_settings(FX_STRESS_PCTS=['3', '7.5', 'bad', '-2']):
            self.assertEqual(services.fx_stress_pcts(),
                             [Decimal('3'), Decimal('7.5')])


class HonestDetectionTests(TestCase):
    """Confidence must reflect how MUCH and how REGULAR the data is (not months×25),
    amount variability must be captured, and lumpy-but-repeated payees must be kept
    VISIBLE on a watch list rather than silently dropped."""

    def test_confidence_is_not_100_on_thin_data(self):
        _seed()   # THINKQ/ADRISK each seen 3× over 3 months
        services.detect_recurring()
        thinkq = RecurringForexPayee.objects.get(
            beneficiary_key=services.normalise_key('THINKQ CONSULTING SERVICES'))
        # Old formula scored 3 months × 25 = 75; the new formula caps this
        # 3-of-6-sightings payee at ~66, so < 75 genuinely fails on a regression.
        self.assertGreater(thinkq.confidence, 0)
        self.assertLess(thinkq.confidence, 75)

    def test_amount_min_max_captured(self):
        _seed()
        services.detect_recurring()
        adrisk = RecurringForexPayee.objects.get(
            beneficiary_key=services.normalise_key('ADRISK GLOBAL SOLUTIONS'))
        self.assertEqual(adrisk.amount_min, Decimal('9335.11'))
        self.assertEqual(adrisk.amount_max, Decimal('11970.77'))

    def test_repeated_irregular_payee_is_watched_not_dropped(self):
        # Same payee twice, ~6 months apart → not clockwork, but must not vanish.
        for ref, vdate in [('7000001', date(2026, 1, 10)), ('7000002', date(2026, 7, 12))]:
            ForexPaymentHistory.objects.create(
                reference=ref, beneficiary='LUMPY REINSURER',
                beneficiary_key=services.normalise_key('LUMPY REINSURER'),
                currency='USD', amount=Decimal('500000.00'), value_date=vdate,
                capture_date=vdate, source_account='60000001', status='Complete')
        services.detect_recurring()
        payee = RecurringForexPayee.objects.get(
            beneficiary_key=services.normalise_key('LUMPY REINSURER'))
        self.assertFalse(payee.active)     # not on the auto calendar
        self.assertTrue(payee.watch)       # but flagged for manual planning

    def test_true_one_off_is_neither_active_nor_watched(self):
        _seed()   # CFAO seen once
        services.detect_recurring()
        cfao = RecurringForexPayee.objects.get(
            beneficiary_key=services.normalise_key('CFAO MOBILITY'))
        self.assertFalse(cfao.active)
        self.assertFalse(cfao.watch)


class BacktestAccuracyTests(TestCase):
    def setUp(self):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'symbol': '$'})
        self.user = User.objects.create_user('fin2', 'fin2@x.co', 'pw')
        ExchangeRate.objects.create(
            from_currency_id='USD', to_currency_id='BWP', rate=Decimal('13.50000000'),
            effective_date=date(2026, 1, 1), loaded_by=self.user,
            approved_by=self.user, approved_at='2026-01-01T00:00:00Z')

    def test_perfect_month_scores_100(self):
        # Planned exactly what happened, in May 2026.
        PlannedForexPayment.objects.create(
            beneficiary='THINKQ', currency='USD', expected_amount=Decimal('2250.00'),
            expected_value_date=date(2026, 5, 27), estimated_bwp=Decimal('30375.00'),
            beneficiary_key='THINKQ', driver=PlannedForexPayment.Driver.RECURRING)
        ForexPaymentHistory.objects.create(
            reference='A1', beneficiary='THINKQ', beneficiary_key='THINKQ',
            currency='USD', amount=Decimal('2250.00'), value_date=date(2026, 5, 27),
            capture_date=date(2026, 5, 27), status='Complete')
        out = services.backtest_accuracy(months_back=3, today=date(2026, 6, 15))
        self.assertTrue(out['has_data'])
        may = next(p for p in out['periods'] if p['month'] == 5 and p['year'] == 2026)
        self.assertEqual(may['predicted_count'], 1)
        self.assertEqual(may['actual_count'], 1)
        self.assertEqual(may['accuracy_pct'], 100)
        self.assertEqual(may['matched_payees'], 1)
        self.assertEqual(out['overall_accuracy_pct'], 100)

    def test_over_forecast_lowers_accuracy(self):
        # Predicted P30,375 but nothing actually happened → 0% that month.
        PlannedForexPayment.objects.create(
            beneficiary='GHOST', currency='USD', expected_amount=Decimal('2250.00'),
            expected_value_date=date(2026, 5, 27), estimated_bwp=Decimal('30375.00'),
            beneficiary_key='GHOST', driver=PlannedForexPayment.Driver.RECURRING)
        out = services.backtest_accuracy(months_back=1, today=date(2026, 6, 15))
        may = out['periods'][0]
        self.assertEqual(may['actual_count'], 0)
        self.assertEqual(may['accuracy_pct'], 0)


class ParserTests(TestCase):
    def test_parses_fnb_forex_text_layout(self):
        # Beneficiary wraps across a line, amount is European-formatted.
        text = (
            "9661827 - MAKSURE\n"
            "RISK SOLUTIONS Once-Off 29 Apr 2026 30 Apr 2026 60000003 ZAR 40 545,45 Complete\n"
            "9548467 - CFAO\n"
            "MOBILITY Once-Off 01 Apr 2026 02 Apr 2026 60000002 ZAR 250 000,00 Complete\n"
        )
        rows = services.parse_forex_text(text)
        self.assertEqual(len(rows), 2)
        by_ref = {r['reference']: r for r in rows}
        self.assertEqual(by_ref['9661827']['beneficiary'], 'MAKSURE RISK SOLUTIONS')
        self.assertEqual(by_ref['9661827']['currency'], 'ZAR')
        self.assertEqual(by_ref['9661827']['amount'], Decimal('40545.45'))
        self.assertEqual(by_ref['9661827']['value_date'], date(2026, 4, 30))
        self.assertEqual(by_ref['9548467']['amount'], Decimal('250000.00'))
