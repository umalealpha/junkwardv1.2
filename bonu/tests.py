"""BONU forensics — the over-billing checks that protect against padded legal bills.

CFO 2026-08-03: "lawyers are crooks and they can cheat us." These tests pin each check
against a bill built to contain exactly one defect, so a rule cannot silently stop firing.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import TestCase

from bonu.forensics import run_rules, summarise
from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm

D = Decimal
JUL = datetime.date(2026, 7, 1)


def firm(name='Crook & Partners', rate='1500'):
    return LawFirm.objects.create(name=name,
                                 agreed_hourly_rate=D(rate) if rate else None)


def invoice(f, number='INV-1', subtotal='0', start=None, end=None):
    return BonuInvoice.objects.create(
        firm=f, invoice_number=number, invoice_date=datetime.date(2026, 7, 31),
        period_start=start, period_end=end, subtotal=D(subtotal), total=D(subtotal))


def line(inv, **kw):
    kw.setdefault('member_ref', 'M-001')
    kw.setdefault('matter_ref', '118/2026')
    kw.setdefault('service_code', 'LITIG')
    kw.setdefault('service_date', JUL)
    kw.setdefault('basis', 'hourly')
    kw.setdefault('units', D('2'))
    kw.setdefault('rate', D('1500'))
    kw.setdefault('amount', D('3000'))
    return BonuInvoiceLine.objects.create(invoice=inv, **kw)


def codes(findings):
    return {f['code'] for f in findings}


class DuplicateTests(TestCase):
    def test_same_matter_same_day_same_service_is_flagged(self):
        f = firm(); inv = invoice(f)
        line(inv, line_no=1)
        line(inv, line_no=2)
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertIn('DUP_MATTER', codes(found))
        dup = next(x for x in found if x['code'] == 'DUP_MATTER')
        self.assertEqual(dup['amount_at_risk'], D('3000'))     # the second charge
        self.assertIn('118/2026', dup['question_for_firm'])

    def test_different_days_on_the_same_matter_is_normal(self):
        f = firm(); inv = invoice(f)
        line(inv, line_no=1)
        line(inv, line_no=2, service_date=datetime.date(2026, 7, 2))
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertNotIn('DUP_MATTER', codes(found))

    def test_three_identical_amounts_same_day_flagged(self):
        f = firm(); inv = invoice(f)
        for i in range(3):
            line(inv, line_no=i, service_code=f'S{i}')     # avoid DUP_MATTER
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertIn('DUP_AMOUNT', codes(found))


class TariffTests(TestCase):
    def test_rate_above_the_agreed_tariff_is_flagged_with_the_overcharge(self):
        f = firm(rate='1500'); inv = invoice(f)
        line(inv, rate=D('2500'), units=D('4'), amount=D('10000'))
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertIn('RATE_OVER_TARIFF', codes(found))
        r = next(x for x in found if x['code'] == 'RATE_OVER_TARIFF')
        self.assertEqual(r['amount_at_risk'], D('4000'))      # (2500-1500) x 4
        self.assertEqual(r['severity'], 'high')

    def test_no_tariff_on_file_means_no_rate_check(self):
        f = firm(rate=None); inv = invoice(f)
        line(inv, rate=D('9999'), amount=D('19998'))
        self.assertNotIn('RATE_OVER_TARIFF',
                         codes(run_rules(BonuInvoiceLine.objects.select_related(
                             'invoice', 'invoice__firm'))))


class ArithmeticTests(TestCase):
    def test_units_times_rate_not_equal_amount(self):
        f = firm(); inv = invoice(f)
        line(inv, units=D('2'), rate=D('1500'), amount=D('4500'))   # should be 3000
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertIn('ARITHMETIC', codes(found))
        a = next(x for x in found if x['code'] == 'ARITHMETIC')
        self.assertEqual(a['amount_at_risk'], D('1500'))

    def test_correct_arithmetic_is_silent(self):
        f = firm(); inv = invoice(f)
        line(inv)
        self.assertNotIn('ARITHMETIC', codes(run_rules(
            BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))))

    def test_invoice_that_does_not_foot(self):
        f = firm(); inv = invoice(f, subtotal='5000')
        line(inv, amount=D('3000'))
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertIn('NOT_FOOTING', codes(found))


class ImpossibleDayTests(TestCase):
    def test_more_than_twelve_hours_in_a_day(self):
        f = firm(); inv = invoice(f)
        for i in range(4):
            line(inv, line_no=i, fee_earner='K. Modise', units=D('4'),
                 service_code=f'S{i}', amount=D('6000'))
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertIn('IMPOSSIBLE_DAY', codes(found))
        d = next(x for x in found if x['code'] == 'IMPOSSIBLE_DAY')
        self.assertIn('16.0 hours', d['title'])

    def test_a_normal_day_is_silent(self):
        f = firm(); inv = invoice(f)
        line(inv, fee_earner='K. Modise', units=D('7'), amount=D('10500'))
        self.assertNotIn('IMPOSSIBLE_DAY', codes(run_rules(
            BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))))


class DateTests(TestCase):
    def test_sunday_work_is_flagged(self):
        f = firm(); inv = invoice(f)
        line(inv, service_date=datetime.date(2026, 7, 5))     # a Sunday
        self.assertIn('NON_WORKING_DAY', codes(run_rules(
            BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))))

    def test_public_holiday_work_is_flagged(self):
        f = firm(); inv = invoice(f)
        line(inv, service_date=datetime.date(2026, 7, 20))
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'),
                          holidays={datetime.date(2026, 7, 20)})
        self.assertIn('NON_WORKING_DAY', codes(found))

    def test_service_date_outside_the_invoice_period(self):
        f = firm()
        inv = invoice(f, start=datetime.date(2026, 7, 1), end=datetime.date(2026, 7, 31))
        line(inv, service_date=datetime.date(2026, 5, 4))
        self.assertIn('OUT_OF_PERIOD', codes(run_rules(
            BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))))


class MemberRefTests(TestCase):
    def test_a_line_with_no_member_reference_is_high_severity(self):
        f = firm(); inv = invoice(f)
        line(inv, member_ref='')
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        m = next(x for x in found if x['code'] == 'NO_MEMBER_REF')
        self.assertEqual(m['severity'], 'high')
        self.assertEqual(m['amount_at_risk'], D('3000'))


class DistributionTests(TestCase):
    def test_round_number_bias_across_a_firms_bills(self):
        f = firm(); inv = invoice(f)
        for i in range(14):
            line(inv, line_no=i, matter_ref=f'M{i}', service_code=f'S{i}',
                 units=None, rate=None, basis='fixed', amount=D('2500'))
        self.assertIn('ROUND_NUMBERS', codes(run_rules(
            BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))))

    def test_varied_realistic_amounts_do_not_trip_it(self):
        f = firm(); inv = invoice(f)
        for i in range(14):
            line(inv, line_no=i, matter_ref=f'M{i}', service_code=f'S{i}',
                 units=None, rate=None, basis='fixed', amount=D(str(1237 + i * 313)))
        self.assertNotIn('ROUND_NUMBERS', codes(run_rules(
            BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))))

    def test_outlier_against_the_firms_own_median(self):
        f = firm(); inv = invoice(f)
        for i in range(14):
            line(inv, line_no=i, matter_ref=f'M{i}', units=None, rate=None,
                 basis='fixed', amount=D('2000'))
        line(inv, line_no=99, matter_ref='BIG', units=None, rate=None,
             basis='fixed', amount=D('40000'))
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertIn('OUTLIER_VS_OWN', codes(found))


class SummaryTests(TestCase):
    def test_summary_totals_the_money_at_risk(self):
        f = firm(); inv = invoice(f)
        line(inv, line_no=1)
        line(inv, line_no=2)                      # duplicate -> 3000 at risk
        s = summarise(run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')))
        self.assertGreaterEqual(s['amount_at_risk'], 3000.0)
        self.assertEqual(s['total'], s['high'] + s['medium'] + s['low'])

    def test_a_clean_bill_produces_nothing(self):
        f = firm(); inv = invoice(f, subtotal='3000')
        line(inv, fee_earner='K. Modise')
        found = run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        self.assertEqual(found, [], f'clean invoice produced {codes(found)}')


class DuplicateInvoiceNumberTests(TestCase):
    def test_the_same_firm_cannot_present_the_same_invoice_number_twice(self):
        from django.db import IntegrityError, transaction
        f = firm()
        invoice(f, number='INV-77')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                invoice(f, number='INV-77')


class FilterAndBreakdownTests(TestCase):
    """CFO 2026-08-03: "Will I be able to filter by lawyer, legal case (for example
    divorce)". These pin that filtering and the by-case-type / by-lawyer breakdowns
    actually work, because before this the answer was no: case type had no field at all
    and the lawyer was captured but not filterable."""

    @classmethod
    def setUpTestData(cls):
        cls.f1 = LawFirm.objects.create(name='Divorce Specialists', agreed_hourly_rate=D('1500'))
        cls.f2 = LawFirm.objects.create(name='Property Transfers Inc', agreed_hourly_rate=D('1200'))
        i1 = BonuInvoice.objects.create(firm=cls.f1, invoice_number='D-1',
                                        invoice_date=datetime.date(2026, 7, 31), subtotal=D('9000'))
        i2 = BonuInvoice.objects.create(firm=cls.f2, invoice_number='P-1',
                                        invoice_date=datetime.date(2026, 7, 31), subtotal=D('6000'))
        mk = BonuInvoiceLine.objects.create
        mk(invoice=i1, line_no=1, matter_ref='D/100', member_ref='M-1', matter_type='divorce',
           fee_earner='T. Molefe', basis='hourly', units=D('2'), rate=D('1500'), amount=D('3000'),
           service_date=datetime.date(2026, 7, 6))
        mk(invoice=i1, line_no=2, matter_ref='D/101', member_ref='M-2', matter_type='divorce',
           fee_earner='T. Molefe', basis='hourly', units=D('2'), rate=D('1500'), amount=D('3000'),
           service_date=datetime.date(2026, 7, 7))
        mk(invoice=i1, line_no=3, matter_ref='C/200', member_ref='M-3', matter_type='criminal',
           fee_earner='B. Kgosi', basis='hourly', units=D('2'), rate=D('1500'), amount=D('3000'),
           service_date=datetime.date(2026, 7, 8))
        mk(invoice=i2, line_no=1, matter_ref='T/300', member_ref='M-4', matter_type='conveyancing',
           fee_earner='L. Sento', basis='fixed', units=None, rate=None, amount=D('6000'),
           service_date=datetime.date(2026, 7, 9))

    def _lines(self, **flt):
        qs = BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')
        return list(qs.filter(**flt))

    def test_case_type_is_a_real_field_with_divorce_in_it(self):
        choices = dict(BonuInvoiceLine.MatterType.choices)
        self.assertIn('divorce', choices)
        self.assertEqual(choices['divorce'], 'Divorce / family')

    def test_filter_by_case_type_divorce(self):
        rows = self._lines(matter_type='divorce')
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.matter_ref for r in rows}, {'D/100', 'D/101'})

    def test_filter_by_individual_lawyer(self):
        self.assertEqual(len(self._lines(fee_earner__icontains='Molefe')), 2)
        self.assertEqual(len(self._lines(fee_earner__icontains='Kgosi')), 1)

    def test_filter_by_firm(self):
        self.assertEqual(len(self._lines(invoice__firm__name__icontains='Property')), 1)

    def test_filter_by_lawyer_and_case_type_together(self):
        rows = self._lines(fee_earner__icontains='Molefe', matter_type='divorce')
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self._lines(fee_earner__icontains='Kgosi', matter_type='divorce')), 0)

    def test_filter_by_member_reference(self):
        self.assertEqual(len(self._lines(member_ref__iexact='M-2')), 1)

    def test_filter_by_service_date_range(self):
        rows = self._lines(service_date__gte=datetime.date(2026, 7, 7),
                           service_date__lte=datetime.date(2026, 7, 8))
        self.assertEqual(len(rows), 2)

    def test_spend_by_case_type_adds_up(self):
        from collections import defaultdict
        agg = defaultdict(float)
        for l in self._lines():
            agg[l.matter_type] += float(l.amount)
        self.assertEqual(agg['divorce'], 6000.0)
        self.assertEqual(agg['criminal'], 3000.0)
        self.assertEqual(agg['conveyancing'], 6000.0)
        self.assertEqual(sum(agg.values()), 15000.0)

    def test_effective_rate_per_lawyer_can_be_computed(self):
        rows = self._lines(fee_earner__icontains='Molefe')
        hours = sum(float(r.units or 0) for r in rows)
        billed = sum(float(r.amount) for r in rows)
        self.assertEqual(hours, 4.0)
        self.assertEqual(billed / hours, 1500.0)

    def test_unclassified_work_defaults_to_other_not_blank(self):
        """An unclassified line must land in 'other', so a filter total never silently
        omits it."""
        i = BonuInvoice.objects.create(firm=self.f1, invoice_number='D-2',
                                      invoice_date=datetime.date(2026, 7, 31))
        l = BonuInvoiceLine.objects.create(invoice=i, matter_ref='X/1', member_ref='M-9',
                                           amount=D('100'))
        self.assertEqual(l.matter_type, 'other')
