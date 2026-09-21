"""
bonu/test_ledger_noise.py — the three false readings the live system produced, locked shut.

All three appeared the moment 620 ledger-derived invoices went in on 2026-08-03, and none
of them were visible in any earlier test because every earlier test used rows WITH invoice
detail. They are the same class of mistake: reporting the absence of data as if it were a
finding about a law firm.

  1. "P6.6M at risk" on P4.8M of spend — the missing-member-reference rule fired on all 620
     ledger rows, whose member reference was deliberately never imported.
  2. "163 cases running hot" — every unclassified matter compared against a median of every
     other unclassified matter.
  3. "0 of 40 cases, P85,000 unearned" — an empty case register read as though the firm had
     done nothing, while its own 111 invoices proved otherwise.
"""
import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from bonu import panel as P
from bonu.retainer import scorecard

D = Decimal


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def firm(pk=1, name='KUBANGA ATTORNEYS', rate=None):
    return Obj(pk=pk, name=name, agreed_hourly_rate=rate, trading_name='', contact_email='')


def line(f, amount, ref='BILL/2025/07/0001', mtype='other', from_ledger=True,
         member='', date=None, units=None, rate=None):
    """A row as the ledger importer creates it, or as a real invoice would."""
    inv = Obj(pk=f'inv-{ref}', firm_id=f.pk, firm=f, invoice_number=ref,
              invoice_date=date or dt.date(2026, 3, 1),
              period_start=None, period_end=None,
              subtotal=D(str(amount)), total=D(str(amount)),
              source_file=('Odoo GL import — account 103014 (BONU Claims)' if from_ledger
                           else 'JT-2026-114.xlsx'))
    row = Obj(pk=f'{ref}-{amount}', invoice=inv, invoice_id=f'inv-{ref}',
              amount=D(str(amount)), matter_type=mtype,
              matter_ref=ref, member_ref=member, service_date=date or dt.date(2026, 3, 1),
              units=units, rate=rate, basis='other', fee_earner='', service_code='',
              matter_type_source='default', matter_description='',
              recomputed=None, arithmetic_ok=None)
    # The footing rule walks invoice.lines.all(), so the stand-in invoice has to answer that.
    inv.lines = Obj(all=lambda _r=row: [_r])
    return row


class LedgerRowsDoNotGenerateNoiseTests(TestCase):
    """Real rows, because these rules walk relations. Stand-ins kept growing new holes."""

    LEDGER = 'Odoo GL import — account 103014 (BONU Claims)'
    REAL = 'JT-2026-114.xlsx'

    def _invoice(self, source, number='BILL/1', amount='4500', date=None,
                 member='', firm_name='KUBANGA ATTORNEYS'):
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        firm, _ = LawFirm.objects.get_or_create(name=firm_name)
        d = date or dt.date(2026, 3, 2)
        inv = BonuInvoice.objects.create(
            firm=firm, invoice_number=number, invoice_date=d,
            subtotal=D(amount), total=D(amount), source_file=source)
        BonuInvoiceLine.objects.create(
            invoice=inv, line_no=1, service_date=d, matter_ref=number,
            member_ref=member, amount=D(amount), basis='other')
        return inv

    def _codes(self):
        from bonu.forensics import run_rules
        from bonu.models import BonuInvoiceLine
        lines = BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')
        return {f['code'] for f in run_rules(lines)}

    def test_a_ledger_row_is_not_flagged_for_a_missing_member_reference(self):
        for i in range(5):
            self._invoice(self.LEDGER, number=f'BILL/{i}', amount=str(4500 + i))
        self.assertNotIn('NO_MEMBER_REF', self._codes())

    def test_a_real_invoice_row_IS_still_flagged_for_a_missing_member_reference(self):
        # The control must keep working where a reference could have been captured,
        # otherwise the fix has quietly switched a real check off.
        self._invoice(self.REAL, number='5131')
        self.assertIn('NO_MEMBER_REF', self._codes())

    def test_a_real_invoice_row_with_a_member_reference_is_not_flagged(self):
        self._invoice(self.REAL, number='5131', member='MB-40921')
        self.assertNotIn('NO_MEMBER_REF', self._codes())

    def test_a_posting_date_on_a_sunday_is_not_called_weekend_work(self):
        sunday = dt.date(2026, 3, 1)
        self.assertEqual(sunday.weekday(), 6)
        self._invoice(self.LEDGER, number='BILL/SUN', date=sunday)
        self.assertNotIn('NON_WORKING_DAY', self._codes())

    def test_real_work_dated_on_a_sunday_is_still_flagged(self):
        sunday = dt.date(2026, 3, 1)
        self._invoice(self.REAL, number='5140', member='MB-1', date=sunday)
        self.assertIn('NON_WORKING_DAY', self._codes())

    def test_money_at_risk_cannot_exceed_the_spend_on_ledger_only_data(self):
        # The live symptom on 2026-08-03: P6.6M at risk against P4.8M billed.
        from bonu.forensics import run_rules
        from bonu.models import BonuInvoiceLine
        for i in range(40):
            self._invoice(self.LEDGER, number=f'BILL/N{i}', amount='1000')
        lines = list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        spend = sum(float(l.amount) for l in lines)
        at_risk = sum(float(f['amount_at_risk']) for f in run_rules(lines))
        self.assertLessEqual(at_risk, spend)


class UnclassifiedIsCountedNotComparedTests(SimpleTestCase):

    def test_unclassified_spend_has_no_typical_cost(self):
        lines = [line(firm(), 1000 * (i + 1), ref=f'BILL/{i}') for i in range(10)]
        row = P.typical_cost_by_matter_type(lines)['other']
        self.assertIsNone(row['typical'])
        self.assertIn('never set', row['basis'])
        # It is still counted — the money is real even when the classification is not.
        self.assertEqual(row['total_spend'], D('55000'))

    def test_no_unclassified_matter_is_called_running_hot(self):
        # 620 rows in one bucket previously produced 163 "hot" cases.
        lines = [line(firm(), 1000, ref=f'BILL/{i}') for i in range(20)]
        lines.append(line(firm(), 90000, ref='BILL/BIG'))
        warnings = P.case_early_warnings(lines)
        self.assertEqual([w for w in warnings if w['running_hot']], [])
        self.assertEqual([w for w in warnings if w['message']], [])

    def test_a_classified_case_is_still_compared_and_still_flagged(self):
        f = firm()
        lines = [line(f, 2000, ref=f'M{i}', mtype='divorce', from_ledger=False) for i in range(6)]
        lines.append(line(f, 8000, ref='HOT', mtype='divorce', from_ledger=False))
        hot = [w for w in P.case_early_warnings(lines) if w['matter_ref'] == 'HOT'][0]
        self.assertTrue(hot['running_hot'])
        self.assertEqual(hot['multiple'], D('4.00'))


class EmptyCaseRegisterIsABlindSpotTests(SimpleTestCase):

    def _retainer(self):
        # Every field scorecard() reads — checked against the source, not guessed at.
        f = firm(name='JEREMIAH TLADI & COMPANY')
        return Obj(name='JEREMIAH TLADI & COMPANY — BONU panel',
                   committed_cases=40, monthly_fee=D('85000'),
                   fee_per_committed_case=D('2125.00'),
                   max_days_no_activity=30, max_days_to_first_action=5,
                   firm=f, firm_id=f.pk)

    def test_no_register_reports_unknown_not_a_full_shortfall(self):
        card = scorecard(self._retainer(), [], dt.date(2026, 8, 3))
        self.assertIsNone(card['retainer_not_earned'])
        self.assertIsNone(card['shortfall_cases'])
        self.assertFalse(card['has_case_register'])

    def test_no_register_asks_for_the_case_list_instead_of_accusing(self):
        card = scorecard(self._retainer(), [], dt.date(2026, 8, 3))
        self.assertIn('No case register yet', card['verdict'])
        self.assertIn('case list', card['verdict'])
        self.assertNotIn('overpaying', card['verdict'])

    def test_with_a_register_the_shortfall_is_quantified_again(self):
        cases = []
        for i in range(24):
            c = Obj(case_ref=f'C{i}', status='active', is_open=True,
                    instructed_on=dt.date(2026, 1, 10), first_action_on=dt.date(2026, 1, 12),
                    last_activity_on=dt.date(2026, 7, 30), next_action_due=None,
                    court_date=None, closed_on=None, matter_type='divorce')
            c.days_quiet = lambda as_of=None: 4
            cases.append(c)
        card = scorecard(self._retainer(), cases, dt.date(2026, 8, 3))
        self.assertEqual(card['shortfall_cases'], 16)
        # 16 cases short x P2,125 = P34,000 of fee bought and not received.
        self.assertEqual(card['retainer_not_earned'], 34000.0)
        self.assertTrue(card['has_case_register'])


class RecurringOutliersAreOneFindingTests(TestCase):
    """A recurring amount is a pattern, not N separate outliers.

    Live on 2026-08-03: one firm billed exactly 40,000 on eleven bills and the screen showed
    eleven identical cards, burying the finding that actually matters — a firm on an 85,000
    monthly retainer also invoicing 40,000 a month.
    """

    def _line(self, amount, ref, code='attendance'):
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        firm, _ = LawFirm.objects.get_or_create(name='JEREMIAH TLADI & COMPANY')
        inv = BonuInvoice.objects.create(firm=firm, invoice_number=ref,
                                         invoice_date=dt.date(2026, 3, 2),
                                         subtotal=D(str(amount)), total=D(str(amount)),
                                         source_file='JT.xlsx')
        return BonuInvoiceLine.objects.create(invoice=inv, line_no=1, matter_ref=ref,
                                              member_ref='MB-1', service_code=code,
                                              amount=D(str(amount)), basis='other',
                                              service_date=dt.date(2026, 3, 2))

    def _outliers(self):
        from bonu.forensics import run_rules
        from bonu.models import BonuInvoiceLine
        lines = BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')
        return [f for f in run_rules(lines) if f['code'] == 'OUTLIER_VS_OWN']

    def test_eleven_identical_charges_produce_one_finding(self):
        for i in range(12):
            self._line(4700, f'N{i}')          # the firm's normal
        for i in range(11):
            self._line(40000, f'BIG{i}')       # the same big amount, eleven times
        f = self._outliers()
        self.assertEqual(len(f), 1)
        self.assertIn('11 times', f[0]['title'])

    def test_a_recurring_charge_is_raised_to_high_and_mentions_the_retainer(self):
        for i in range(12):
            self._line(4700, f'N{i}')
        for i in range(11):
            self._line(40000, f'BIG{i}')
        f = self._outliers()[0]
        self.assertEqual(f['severity'], 'high')
        self.assertIn('retainer', f['detail'].lower())

    def test_the_money_covers_every_occurrence_not_just_one(self):
        for i in range(12):
            self._line(4700, f'N{i}')
        for i in range(11):
            self._line(40000, f'BIG{i}')
        f = self._outliers()[0]
        # (40,000 - 4,700) x 11 = 388,300
        self.assertEqual(f['amount_at_risk'], D('388300'))

    def test_a_single_one_off_outlier_stays_medium(self):
        for i in range(12):
            self._line(4700, f'N{i}')
        self._line(40000, 'ONEOFF')
        f = self._outliers()[0]
        self.assertEqual(f['severity'], 'medium')
        self.assertIn('once', f['title'])

    def test_two_different_big_amounts_stay_two_findings(self):
        for i in range(12):
            self._line(4700, f'N{i}')
        self._line(40000, 'A')
        self._line(30000, 'B')
        self.assertEqual(len(self._outliers()), 2)


class AgreedSlaFeeIsNotAnOverchargeTests(TestCase):
    """The agreed monthly fee is what we contracted to pay, not a finding against the firm.

    CFO 2026-08-03: "the 40,000 monthly its a sla we have with the legal firm to manage 40
    clients cost control." Before this, that recurring fee was the single largest item on the
    forensics screen — the system was accusing a firm of over-billing for charging exactly the
    amount we agreed.
    """

    FEE = D('40000')

    def _firm(self):
        from bonu.models import LawFirm
        f, _ = LawFirm.objects.get_or_create(name='JEREMIAH TLADI & COMPANY')
        return f

    def _line(self, amount, ref, date, code='attendance'):
        from bonu.models import BonuInvoice, BonuInvoiceLine
        inv = BonuInvoice.objects.create(firm=self._firm(), invoice_number=ref, invoice_date=date,
                                         subtotal=D(str(amount)), total=D(str(amount)),
                                         source_file='JT.xlsx')
        return BonuInvoiceLine.objects.create(invoice=inv, line_no=1, matter_ref=ref,
                                              member_ref='MB-1', service_code=code,
                                              amount=D(str(amount)), basis='other',
                                              service_date=date)

    def _run(self, fees=None):
        from bonu.forensics import run_rules
        from bonu.models import BonuInvoiceLine
        lines = BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')
        return run_rules(lines, retainer_fees=(fees if fees is not None else
                                               {str(self._firm().pk): self.FEE}))

    def _months(self, n, amount=None):
        for i in range(n):
            self._line(amount if amount is not None else self.FEE, f'FEE{i}',
                       dt.date(2025, 7, 1) + dt.timedelta(days=31 * i))

    def test_the_agreed_fee_is_not_reported_as_an_outlier(self):
        for i in range(12):
            self._line(4700, f'N{i}', dt.date(2026, 3, 2))
        self._months(10)
        codes = {f['code'] for f in self._run()}
        self.assertNotIn('OUTLIER_VS_OWN', codes)

    def test_without_the_fee_on_file_it_still_reads_as_an_outlier(self):
        # Proof the suppression is driven by the agreement, not by the amount.
        for i in range(12):
            self._line(4700, f'N{i}', dt.date(2026, 3, 2))
        self._months(10)
        codes = {f['code'] for f in self._run(fees={})}
        self.assertIn('OUTLIER_VS_OWN', codes)

    def test_ten_months_of_fee_reads_as_agreed_with_no_money_at_risk(self):
        self._months(10)
        f = [x for x in self._run() if x['code'] == 'SLA_FEE_AS_AGREED']
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['severity'], 'low')
        self.assertEqual(f[0]['amount_at_risk'], D('0'))
        self.assertIn('as agreed', f[0]['title'])

    def test_the_same_month_charged_twice_is_money_back(self):
        self._line(self.FEE, 'A', dt.date(2026, 1, 5))
        self._line(self.FEE, 'B', dt.date(2026, 1, 20))     # same month, second fee
        f = [x for x in self._run() if x['code'] == 'SLA_FEE_TWICE_IN_A_MONTH']
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['severity'], 'high')
        self.assertEqual(f[0]['amount_at_risk'], self.FEE)
        self.assertIn('credit', f[0]['question_for_firm'].lower())

    def test_a_charge_that_is_not_the_fee_is_still_checked(self):
        for i in range(12):
            self._line(4700, f'N{i}', dt.date(2026, 3, 2))
        self._months(10)
        self._line(75000, 'BIG', dt.date(2026, 2, 2))
        titles = ' '.join(x['title'] for x in self._run() if x['code'] == 'OUTLIER_VS_OWN')
        self.assertIn('75,000', titles)


class TwoSlasWithOneFirmTests(TestCase):
    """One firm, two agreements. CFO 2026-08-03: "the same legal firm has two SLAs. One is for
    south for 40,000 BWP and somewhere around 85,000 to manage north so don't get confused."

    Keyed on a single fee per firm, the second agreement would still have been reported as
    over-billing — which is the same false accusation, just for the other region.
    """

    SOUTH = D('40000')
    NORTH = D('85000')

    def _firm(self):
        from bonu.models import LawFirm
        f, _ = LawFirm.objects.get_or_create(name='JEREMIAH TLADI & COMPANY')
        return f

    def _line(self, amount, ref, date):
        from bonu.models import BonuInvoice, BonuInvoiceLine
        inv = BonuInvoice.objects.create(firm=self._firm(), invoice_number=ref, invoice_date=date,
                                         subtotal=D(str(amount)), total=D(str(amount)))
        return BonuInvoiceLine.objects.create(invoice=inv, line_no=1, matter_ref=ref,
                                              member_ref='MB-1', service_code='attendance',
                                              amount=D(str(amount)), basis='other',
                                              service_date=date)

    def _run(self, fees):
        from bonu.forensics import run_rules
        from bonu.models import BonuInvoiceLine
        return run_rules(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'),
                         retainer_fees=fees)

    def _both(self):
        for i in range(12):
            self._line(4700, f'N{i}', dt.date(2026, 3, 2))       # the firm's per-matter normal
        for i in range(6):
            d = dt.date(2025, 8, 1) + dt.timedelta(days=31 * i)
            self._line(self.SOUTH, f'S{i}', d)
            self._line(self.NORTH, f'NO{i}', d)
        return {str(self._firm().pk): [self.SOUTH, self.NORTH]}

    def test_neither_agreed_fee_is_reported_as_an_overcharge(self):
        fees = self._both()
        self.assertNotIn('OUTLIER_VS_OWN', {f['code'] for f in self._run(fees)})

    def test_each_agreement_gets_its_own_as_agreed_line(self):
        fees = self._both()
        agreed = [f for f in self._run(fees) if f['code'] == 'SLA_FEE_AS_AGREED']
        self.assertEqual(len(agreed), 2)
        titles = ' '.join(f['title'] for f in agreed)
        self.assertIn('40,000', titles)
        self.assertIn('85,000', titles)

    def test_recording_only_one_agreement_still_accuses_the_other(self):
        # The failure this test exists to prevent: with only the southern fee on file, the
        # northern fee reads as over-billing.
        self._both()
        codes = {f['code'] for f in self._run({str(self._firm().pk): [self.SOUTH]})}
        self.assertIn('OUTLIER_VS_OWN', codes)

    def test_a_third_amount_is_still_checked(self):
        fees = self._both()
        self._line(120000, 'BIG', dt.date(2026, 2, 2))
        titles = ' '.join(f['title'] for f in self._run(fees) if f['code'] == 'OUTLIER_VS_OWN')
        self.assertIn('120,000', titles)

    def test_the_agreed_fees_do_not_inflate_the_firms_usual_figure(self):
        # Six fees of 40,000 and six of 85,000 alongside twelve charges of 4,700 would move the
        # median to 22,350 if left in the sample — and a real 120,000 charge would then sit under
        # the outlier threshold and never be seen.
        fees = self._both()
        self._line(120000, 'BIG', dt.date(2026, 2, 2))
        f = [x for x in self._run(fees) if x['code'] == 'OUTLIER_VS_OWN'][0]
        self.assertIn('4,700', f['title'])
        self.assertIn('left out of that average', f['detail'])

    def test_one_regions_fee_billed_twice_in_a_month_is_still_caught(self):
        fees = self._both()
        self._line(self.SOUTH, 'S-DUP', dt.date(2025, 8, 20))    # second southern fee, same month
        f = [x for x in self._run(fees) if x['code'] == 'SLA_FEE_TWICE_IN_A_MONTH']
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['amount_at_risk'], self.SOUTH)
