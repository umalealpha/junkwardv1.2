"""commissions/test_review_flags.py — reviewer sanity flags (CFO 2026-08-22,
"make the checkers' life better", phase 1).

Run: SECRET_KEY=x python manage.py test commissions.test_review_flags
(inside the prod container use the PG test DB; sqlite dies on a raw-SQL migration.)
"""
from decimal import Decimal

from django.test import TestCase

from .models import CommissionGroup, CommissionAgent, CommissionSubmission, CommissionSubmissionLine
from .review_flags import review_flags

S = CommissionSubmission.Status


def _line(sub, **kw):
    kw.setdefault('policy_number', 'P1')
    kw.setdefault('commission_amount', Decimal('100'))
    return CommissionSubmissionLine.objects.create(submission=sub, **kw)


class ReviewFlagsTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.get(key='in_house')
        self.agent = CommissionAgent.objects.create(name='Flag Agent', group=self.grp)

    def _sub(self, period='2026-08', gross='100', status=S.SUBMITTED):
        return CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label=period,
            status=status, gross_commission=Decimal(gross))

    def test_clean_sheet_is_clean(self):
        sub = self._sub(gross='100')
        _line(sub, policy_number='P1', amount_applicable=Decimal('1000'),
              commission_rate=Decimal('10'), commission_amount=Decimal('100'))  # 1000*10% = 100
        r = review_flags(sub)
        self.assertEqual(r['level'], 'clean')
        self.assertEqual(r['items'], [])

    def test_duplicate_policy_flagged(self):
        sub = self._sub(gross='200')
        _line(sub, policy_number='DUP1', commission_amount=Decimal('100'))
        _line(sub, policy_number='DUP1', commission_amount=Decimal('100'))
        r = review_flags(sub)
        self.assertEqual(r['level'], 'check')
        self.assertTrue(any('DUP1' in i and 'more than one line' in i for i in r['items']))

    def test_zero_commission_line_flagged(self):
        sub = self._sub(gross='0')
        _line(sub, policy_number='ZERO1', commission_amount=Decimal('0'))
        r = review_flags(sub)
        self.assertTrue(any('ZERO1' in i and 'no commission' in i for i in r['items']))

    def test_total_mismatch_flagged(self):
        sub = self._sub(gross='999')                       # gross deliberately != lines
        _line(sub, policy_number='P1', commission_amount=Decimal('100'))
        r = review_flags(sub)
        self.assertTrue(any("doesn't match the lines" in i for i in r['items']))

    def test_rate_vs_commission_mismatch_flagged(self):
        sub = self._sub(gross='500')
        # 1000 @ 10 => 100 (%) or 10000 (fraction); 500 fits NEITHER → flag
        _line(sub, policy_number='RATE1', amount_applicable=Decimal('1000'),
              commission_rate=Decimal('10'), commission_amount=Decimal('500'))
        r = review_flags(sub)
        self.assertTrue(any('RATE1' in i and 'rate x premium' in i for i in r['items']))

    def test_rate_ambiguity_does_not_cry_wolf(self):
        # rate entered as a FRACTION (0.10) → 1000*0.10 = 100 fits; must be clean
        sub = self._sub(gross='100')
        _line(sub, policy_number='FR1', amount_applicable=Decimal('1000'),
              commission_rate=Decimal('0.10'), commission_amount=Decimal('100'))
        self.assertEqual(review_flags(sub)['level'], 'clean')

    def test_month_on_month_spike_flagged(self):
        CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-07',
            status=S.APPROVED, gross_commission=Decimal('800'))
        sub = self._sub(period='2026-08', gross='2250')
        _line(sub, policy_number='P1', amount_applicable=Decimal('22500'),
              commission_rate=Decimal('10'), commission_amount=Decimal('2250'))
        r = review_flags(sub)
        self.assertTrue(any('vs last month' in i for i in r['items']))

    def test_failure_returns_unknown_not_clean(self):
        # H6 — a crashed check must NEVER read as 'clean' (a false green tick).
        from unittest import mock
        sub = self._sub(gross='100')
        _line(sub, policy_number='P1', commission_amount=Decimal('100'))
        with mock.patch('commissions.review_flags._prior_gross', side_effect=RuntimeError('boom')):
            r = review_flags(sub)
        self.assertEqual(r['level'], 'unknown')

    def test_serializer_gates_on_review_state(self):
        from .serializers import CommissionSubmissionSerializer
        draft = self._sub(period='2026-06', status=S.DRAFT, gross='0')
        _line(draft, policy_number='DUP', commission_amount=Decimal('0'))
        _line(draft, policy_number='DUP', commission_amount=Decimal('0'))
        # a draft is not being reviewed → checks not run; 'unknown' (no badge),
        # never a false 'clean', even though it has dups
        self.assertEqual(CommissionSubmissionSerializer(draft).data['review_flags'],
                         {'level': 'unknown', 'items': []})
        sub = self._sub(period='2026-08', status=S.SUBMITTED, gross='0')
        _line(sub, policy_number='DUP', commission_amount=Decimal('0'))
        _line(sub, policy_number='DUP', commission_amount=Decimal('0'))
        self.assertEqual(CommissionSubmissionSerializer(sub).data['review_flags']['level'], 'check')
