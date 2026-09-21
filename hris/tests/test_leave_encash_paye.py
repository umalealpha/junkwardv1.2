"""PAYE on a leave encashment (CFO directive 2026-08-17).

A cash-out to a SERVING employee is ordinary taxable employment income in the
month it is paid, so the gross is never paid across: tax and net are computed
server-side at the employee's OWN marginal rate and snapshotted on the row.

Bands used are the schedule in force from 1 July 2026 — the Income Tax Act 2026
added a sixth band, 27.5 % on annual taxable income above P400,000.

Companion to test_leave_encashment.py (the chain / valuation / SoD tests).
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import Company
from hris.models import HRISProfile, LeaveOpeningBalance
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            PayslipLine, TaxBracket)

# Every fixture below is hired well before the current leave year, so the
# accrual maths these tests assert on is unaffected by hire-date bounding
# (`leave_balance.accrual_start`) and `apply_encashment` does not refuse
# them for a missing start date. CFO 2026-09-07.
_HIRED = dt.date(2020, 1, 1)

URL = '/hris/api/leave-encashment/'

BANDS_2026 = [
    (Decimal('0'),      Decimal('48000'),  Decimal('0'),     Decimal('0')),
    (Decimal('48000'),  Decimal('84000'),  Decimal('0'),     Decimal('5')),
    (Decimal('84000'),  Decimal('120000'), Decimal('1800'),  Decimal('12.5')),
    (Decimal('120000'), Decimal('156000'), Decimal('6300'),  Decimal('18.75')),
    (Decimal('156000'), Decimal('400000'), Decimal('13050'), Decimal('25')),
    (Decimal('400000'), None,              Decimal('74050'), Decimal('27.5')),
]

REASON = (
    'I am requesting to encash a portion of my accrued annual leave because '
    'I have unavoidable personal financial commitments this month and I will '
    'not be able to take the leave as time off given the current operational '
    'workload in my department, so I would prefer to convert these specific '
    'days into cash rather than carry them forward to the next leave cycle '
    'where they might otherwise be lost or forfeited entirely.')


class LeaveEncashmentPayeTest(APITestCase):

    def setUp(self):
        TaxBracket.objects.all().update(is_active=False)
        for lo, hi, base, rate in BANDS_2026:
            TaxBracket.objects.create(
                name='2026-27 test', lower_bound=lo, upper_bound=hi,
                base_amount=base, rate_pct=rate, is_active=True,
                effective_from=dt.date(2026, 7, 1))

        self.company = Company.objects.create(code='PAYE', name='PAYE Test Co.')
        self.period = PayrollPeriod.objects.create(
            period_name='2099-02', start_date=dt.date(2099, 2, 1),
            end_date=dt.date(2099, 2, 28))
        self.basic_comp = PayslipComponent.objects.create(
            code='BASIC', name='Basic Salary', kind='earning', is_taxable=True)

    def _staff(self, *, username, basic, days_available=60):
        """An employee with a profile, a leave balance and a payslip BASIC."""
        user = User.objects.create_user(username, f'{username}@test.example', 'x')
        emp = Employee.objects.create(
            employee_number=f'P-{username}', full_name=f'{username} Person',
            company=self.company, status='active', hire_date=_HIRED, user=user,
            email=f'{username}@test.example')
        profile = HRISProfile.objects.create(employee=emp)
        # as-at TODAY so the available balance is exactly days_available. Dated a
        # month back, the annual accrual engine added a month's worth and pushed
        # the encashable maximum (and the half-day step count) off, failing the
        # cap/query-count tests at a month-end. See test_leave_encashment.py.
        LeaveOpeningBalance.objects.create(
            profile=profile, leave_type_code='annual',
            entitlement_days=Decimal(days_available),
            opening_balance_days=Decimal(days_available),
            accrued_days=Decimal(days_available),
            as_at_date=dt.date.today())
        PayslipLine.objects.create(
            payslip=Payslip.objects.create(
                employee=emp, period=self.period, company=self.company,
                status=Payslip.Status.DRAFT),
            component=self.basic_comp, amount=Decimal(basic))
        return user

    def _apply(self, user, days):
        c = APIClient()
        c.force_authenticate(user)
        return c.post(URL, {'days': days, 'reason': REASON}, format='json')

    def _quote(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c.get(URL).json()['quote']

    # ── the CFO's own case from the 17-Aug screenshot ───────────────────────
    def test_cfo_case_gross_tax_net(self):
        """Basic P70,000 -> daily 2,916.67 -> 15 days = 43,750.05 gross.
        P840,000/yr sits in the 27.5 % band, so tax = 12,031.26 and
        net = 31,718.79. (Under the OLD 25 %: 10,937.51 / 32,812.54.)"""
        user = self._staff(username='topearner', basic='70000')
        r = self._apply(user, '15')
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual(b['daily_rate'], '2916.67')
        self.assertEqual(b['amount'],     '43750.05')
        self.assertEqual(b['tax_amount'], '12031.26')
        self.assertEqual(b['net_amount'], '31718.79')
        # tax + net must reconcile to the gross, to the cent — always.
        self.assertEqual(Decimal(b['tax_amount']) + Decimal(b['net_amount']),
                         Decimal(b['amount']))

    def test_junior_taxed_at_own_low_rate_not_the_top_rate(self):
        """The point of the marginal method: a junior on P8,000/month is in the
        12.5 % band and must NOT be charged the top 27.5 %."""
        user = self._staff(username='junior', basic='8000')
        b = self._apply(user, '12').json()
        self.assertEqual(b['amount'], '3999.96')       # (8000/24 = 333.33) x 12
        self.assertEqual(b['tax_amount'], '500.00')    # 12.5 %, not 27.5 %
        self.assertEqual(b['net_amount'], '3499.96')
        self.assertLess(Decimal(b['tax_amount']),
                        Decimal(b['amount']) * Decimal('0.275'))

    def test_below_threshold_employee_pays_no_tax(self):
        """P3,000/month = P36,000/yr — under the P48,000 tax-free threshold, so
        net == gross. A flat rate would have wrongly taxed this person."""
        user = self._staff(username='lowpaid', basic='3000')
        b = self._apply(user, '12').json()
        self.assertEqual(b['tax_amount'], '0.00')
        self.assertEqual(b['net_amount'], b['amount'])

    def test_tax_snapshot_frozen_against_a_later_rate_change(self):
        """An approved encashment must not silently reprice when BURS rates move
        — the same discipline the payout amount already has."""
        from hris.leave_encash_models import LeaveEncashment
        user = self._staff(username='frozen', basic='70000')
        enc_id = self._apply(user, '15').json()['id']
        TaxBracket.objects.all().update(is_active=False)   # rates yanked
        enc = LeaveEncashment.objects.get(pk=enc_id)
        self.assertEqual(enc.tax_amount, Decimal('12031.26'))
        self.assertEqual(enc.net_amount, Decimal('31718.79'))

    def test_quote_schedule_matches_what_apply_actually_charges(self):
        """The form previews tax from a server-computed schedule. Every entry
        must equal what apply() charges for those days — a mismatch means the
        employee saw one net figure and was paid another."""
        user = self._staff(username='preview', basic='70000')
        schedule = self._quote(user)['tax_by_days']
        self.assertIn('15', schedule)
        self.assertIn('2.5', schedule)                 # half-days present
        self.assertEqual(schedule['15']['tax'], '12031.26')
        self.assertEqual(schedule['15']['net'], '31718.79')
        applied = self._apply(user, '2.5').json()
        self.assertEqual(schedule['2.5']['payout'], applied['amount'])
        self.assertEqual(schedule['2.5']['tax'],    applied['tax_amount'])
        self.assertEqual(schedule['2.5']['net'],    applied['net_amount'])

    def test_schedule_stops_at_the_encashable_maximum(self):
        """No preview for days the employee may not cash out (balance less the
        10-day HR minimum), so the form can never show an illegal net."""
        user = self._staff(username='capped', basic='20000', days_available=20)
        q = self._quote(user)
        self.assertEqual(Decimal(q['max_encashable_days']), Decimal('10'))
        self.assertIn('10', q['tax_by_days'])
        self.assertNotIn('10.5', q['tax_by_days'])
        self.assertNotIn('20', q['tax_by_days'])

    def test_no_brackets_seeded_means_zero_tax_never_a_crash(self):
        """Same fail-open behaviour as payroll: no active bands => tax 0, and the
        application still succeeds rather than 500-ing."""
        TaxBracket.objects.all().update(is_active=False)
        user = self._staff(username='nobands', basic='70000')
        r = self._apply(user, '15')
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual(b['tax_amount'], '0.00')
        self.assertEqual(b['net_amount'], b['amount'])

    def test_pretax_deduction_lowers_the_tax_base(self):
        """A pension contribution is tax-deductible, so it reduces the base the
        cash-out is taxed on. The payout itself (BASIC / 24) is unchanged.

        Basic P13,200/month = P158,400/yr, just inside the 25 % band. A P1,000
        pension brings taxable pay to P146,400/yr — the 18.75 % band — so the
        SAME gross payout attracts less tax and pays out more."""
        pension = PayslipComponent.objects.create(
            code='PENEE', name='Pension EE', kind='employee_pretax')
        plain = self._staff(username='nopension', basic='13200')
        withp = self._staff(username='haspension', basic='13200')
        slip = Payslip.objects.filter(employee__user=withp).first()
        PayslipLine.objects.create(payslip=slip, component=pension,
                                   amount=Decimal('-1000.00'))
        a = self._apply(plain, '12').json()
        b = self._apply(withp, '12').json()
        self.assertEqual(a['amount'], b['amount'])                  # same gross
        self.assertEqual(a['tax_base'], '13200.00')
        self.assertEqual(b['tax_base'], '12200.00')
        self.assertEqual(a['tax_amount'], '1650.00')                # 25 % x 6,600
        self.assertEqual(b['tax_amount'], '1237.50')                # 18.75 % x 6,600
        self.assertGreater(Decimal(b['net_amount']), Decimal(a['net_amount']))

    def test_tax_and_net_appear_on_the_approver_payload(self):
        """Approvers and Finance must SEE the net — that is the figure loaded for
        payment. If it is missing from the API the card can't show it."""
        user = self._staff(username='visible', basic='70000')
        self._apply(user, '15')
        c = APIClient()
        c.force_authenticate(user)
        row = c.get(URL).json()['requests'][0]
        for field in ('amount', 'tax_base', 'tax_amount', 'net_amount'):
            self.assertIn(field, row)
        self.assertEqual(row['net_amount'], '31718.79')


class RepriceUntaxedEncashmentsTest(LeaveEncashmentPayeTest):
    """`reprice_untaxed_encashments` — puts PAYE on in-flight rows raised before
    withholding existed (prod has 4 such rows as at 17-Aug-2026, one of them the
    CFO's own P58,333.40 pending-CFO application).

    Inherits the fixtures/helpers from LeaveEncashmentPayeTest.
    """

    def _make_untaxed(self, user, days, status, *, paid=False):
        """An application as it looked BEFORE PAYE: net = gross, tax = 0 (exactly
        what migration 0068 backfills)."""
        from hris.leave_encash_models import LeaveEncashment
        enc = LeaveEncashment.objects.get(pk=self._apply(user, days).json()['id'])
        enc.status = status
        enc.payroll_processed = paid
        enc.tax_base = Decimal('0.00')
        enc.tax_amount = Decimal('0.00')
        enc.net_amount = enc.amount
        enc.save()
        return enc

    def _run(self, **opts):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('reprice_untaxed_encashments', stdout=out, **opts)
        return out.getvalue()

    def test_in_flight_row_gets_taxed(self):
        from hris.leave_encash_models import LeaveEncashment
        user = self._staff(username='inflight', basic='70000')
        enc = self._make_untaxed(user, '15', LeaveEncashment.Status.PENDING_HR)
        self.assertEqual(enc.net_amount, enc.amount)      # untaxed to start

        self._run()
        enc.refresh_from_db()
        self.assertEqual(enc.amount, Decimal('43750.05'))  # GROSS unchanged
        self.assertEqual(enc.days, Decimal('15.00'))       # days unchanged
        self.assertEqual(enc.tax_amount, Decimal('12031.26'))
        self.assertEqual(enc.net_amount, Decimal('31718.79'))

    def test_paid_and_rejected_rows_are_never_touched(self):
        """History must not be rewritten — Unami's 3-Aug payout was genuinely
        paid gross with no PAYE, and a rejected row was never going to pay."""
        from hris.leave_encash_models import LeaveEncashment
        paid_user = self._staff(username='alreadypaid', basic='70000')
        rej_user = self._staff(username='wasrejected', basic='70000')
        paid = self._make_untaxed(paid_user, '11', LeaveEncashment.Status.PAID, paid=True)
        rej = self._make_untaxed(rej_user, '10', LeaveEncashment.Status.REJECTED)

        self._run()
        for row in (paid, rej):
            row.refresh_from_db()
            with self.subTest(status=row.status):
                self.assertEqual(row.tax_amount, Decimal('0.00'))
                self.assertEqual(row.net_amount, row.amount)

    def test_dry_run_changes_nothing(self):
        from hris.leave_encash_models import LeaveEncashment
        user = self._staff(username='dryrun', basic='70000')
        enc = self._make_untaxed(user, '15', LeaveEncashment.Status.PENDING_HR)
        output = self._run(dry_run=True)
        enc.refresh_from_db()
        self.assertEqual(enc.tax_amount, Decimal('0.00'))
        self.assertEqual(enc.net_amount, enc.amount)
        self.assertIn('DRY RUN', output)

    def test_already_taxed_rows_are_left_alone(self):
        """Re-running must be safe — it must not re-tax an already-taxed row."""
        user = self._staff(username='alreadytaxed', basic='70000')
        enc_id = self._apply(user, '15').json()['id']
        self._run()
        from hris.leave_encash_models import LeaveEncashment
        enc = LeaveEncashment.objects.get(pk=enc_id)
        self.assertEqual(enc.tax_amount, Decimal('12031.26'))
        self.assertEqual(enc.net_amount, Decimal('31718.79'))

    def test_row_with_no_payslip_is_skipped_not_zeroed(self):
        """No payslip to tax against => leave it at net = gross rather than guess
        (and never leave a net of 0.00 behind)."""
        from hris.leave_encash_models import LeaveEncashment
        user = self._staff(username='nopayslip', basic='70000')
        enc = self._make_untaxed(user, '15', LeaveEncashment.Status.PENDING_HR)
        Payslip.objects.filter(employee=enc.employee).delete()

        output = self._run()
        enc.refresh_from_db()
        self.assertEqual(enc.tax_amount, Decimal('0.00'))
        self.assertEqual(enc.net_amount, enc.amount)
        self.assertIn('SKIPPED', output)


class QuoteQueryCountTest(LeaveEncashmentPayeTest):
    """The quote schedule must fetch the tax bracket rows ONCE, not once per
    half-day step (Fable 5 / OpenAI K5, 2026-08-17: up to 400 identical SELECTs
    per page load). Guards against reintroducing the per-iteration query.
    """

    def test_bracket_table_is_queried_once_per_quote(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        # 60 days available, 10 held back -> 50 encashable -> 100 half-day steps.
        user = self._staff(username='querycount', basic='70000', days_available=60)
        with CaptureQueriesContext(connection) as ctx:
            quote = self._quote(user)
        self.assertEqual(len(quote['tax_by_days']), 100)   # the loop really ran
        bracket_queries = [q for q in ctx.captured_queries
                           if 'payroll_taxbracket' in q['sql']]
        self.assertLessEqual(
            len(bracket_queries), 2,
            f"tax brackets queried {len(bracket_queries)}x building a "
            f"100-step schedule — fetch them once and pass them in")
