"""The forgiveness dashboard (CFO 2026-09-09).

A watching report, not a scoring one. These tests pin the judgements that
decide who a manager looks at first — get the ranking wrong and the dashboard
quietly points at the wrong people before the November reviews.
"""
import datetime as dt

from django.test import TestCase

from hris.late_notice_models import LateNotice
from hris.late_notice_report import MONTHLY_ALLOWANCE, collect

D = dt.date
T = dt.time


class ReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from payroll.models import Employee
        from hris.models import HRISProfile
        cls.profiles = {}
        for i, name in enumerate(('Routine Rita', 'Occasional Ozzy', 'Latefiler Lena')):
            # employee_number is unique and blank collides — give each a distinct one.
            e = Employee.objects.create(full_name=name,
                                        employee_number=f'LNTEST{i:03d}',
                                        email=f'{name.split()[0].lower()}@example.test')
            cls.profiles[name] = HRISProfile.objects.create(employee=e)

    def _n(self, name, day, month=9, in_time=True, filed=T(7, 30)):
        return LateNotice.objects.create(
            profile=self.profiles[name], notice_date=D(2026, month, day),
            kind=LateNotice.Kind.LATE, in_time=in_time, filed_local_time=filed)

    def test_no_notices_is_an_empty_report_not_a_crash(self):
        r = collect(today=D(2026, 9, 30))
        self.assertEqual(r['people'], [])
        self.assertEqual(r['totals']['notices'], 0)

    def test_counts_and_totals(self):
        self._n('Routine Rita', 1); self._n('Routine Rita', 2)
        self._n('Occasional Ozzy', 3)
        r = collect(today=D(2026, 9, 30))
        self.assertEqual(r['totals']['people'], 2)
        self.assertEqual(r['totals']['notices'], 3)

    def test_hitting_the_monthly_allowance_is_flagged(self):
        for d in range(1, MONTHLY_ALLOWANCE + 1):
            self._n('Routine Rita', d)
        r = collect(today=D(2026, 9, 30))
        rita = next(p for p in r['people'] if p['name'] == 'Routine Rita')
        self.assertTrue(rita['hit_the_cap'])
        self.assertEqual(rita['worst_month'], MONTHLY_ALLOWANCE)

    def test_two_in_a_month_is_not_at_the_cap(self):
        self._n('Occasional Ozzy', 1); self._n('Occasional Ozzy', 2)
        r = collect(today=D(2026, 9, 30))
        ozzy = next(p for p in r['people'] if p['name'] == 'Occasional Ozzy')
        self.assertFalse(ozzy['hit_the_cap'])

    def test_three_in_ONE_month_outranks_more_spread_thinly(self):
        """The whole point of the ranking: somebody at the line in a single
        month matters more than somebody with a higher total spread out."""
        for d in (1, 2, 3):
            self._n('Routine Rita', d, month=9)
        for m, d in ((7, 1), (7, 2), (8, 1), (8, 2)):
            self._n('Occasional Ozzy', d, month=m)
        r = collect(months=4, today=D(2026, 9, 30))
        self.assertEqual(r['people'][0]['name'], 'Routine Rita')
        self.assertGreater(
            next(p for p in r['people'] if p['name'] == 'Occasional Ozzy')['total'],
            next(p for p in r['people'] if p['name'] == 'Routine Rita')['total'])

    def test_filing_just_before_the_cutoff_is_counted_separately(self):
        """08:58 every day follows the rule and defeats it. The count alone
        cannot show that; the filing time can."""
        self._n('Latefiler Lena', 1, filed=T(8, 58))
        self._n('Latefiler Lena', 2, filed=T(8, 51))
        self._n('Latefiler Lena', 3, filed=T(6, 40))   # a genuine early heads-up
        lena = next(p for p in collect(today=D(2026, 9, 30))['people']
                    if p['name'] == 'Latefiler Lena')
        self.assertEqual(lena['near_cutoff'], 2)

    def test_out_of_time_notices_are_separated_from_forgiven_ones(self):
        """Somebody whose notices are all too late is already being marked —
        they need telling, not nailing."""
        self._n('Latefiler Lena', 1, in_time=False, filed=T(11, 0))
        self._n('Latefiler Lena', 2, in_time=True)
        lena = next(p for p in collect(today=D(2026, 9, 30))['people']
                    if p['name'] == 'Latefiler Lena')
        self.assertEqual((lena['in_time'], lena['out_of_time']), (1, 1))
        self.assertEqual(collect(today=D(2026, 9, 30))['totals']['filed_too_late'], 1)

    def test_the_report_deducts_nothing(self):
        """It is a watching report. If it ever grows a score, that is a
        decision the CFO has to take, not a side effect."""
        self._n('Routine Rita', 1)
        r = collect(today=D(2026, 9, 30))
        for p in r['people']:
            self.assertNotIn('points', p)
            self.assertNotIn('rating', p)
