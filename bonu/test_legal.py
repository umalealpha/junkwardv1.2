"""The legal office's arithmetic, and the guards around what can be typed into it.

Two bonuses are calculated on this screen, so the tests below are written against
the ways the figures could be quietly WRONG rather than against the happy path:

* an unmapped service read as a zero saving instead of as "no comparison",
* a typo read as zero by one code path and as money by another,
* a bill reviewed before it arrived,
* a quarter measured over anything other than the calendar quarter,
* a bonus paid on a quarter that never reached its trigger.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from bonu import legal_calc as calc
from bonu.models import (LegalAdvisoryEntry, LegalFeeNote, LegalInvoiceSaving,
                         LegalMonthlyBonus, LegalRateMapping, LegalSettings,
                         LegalTariffItem)
from core.models import Company, UserProfile

D = Decimal


def _mapping(desc, basis, external, *, disbursement=False, internal=Decimal('0')):
    return LegalRateMapping.objects.create(
        fee_description=desc, calc_basis=basis, external_rate=D(str(external)),
        internal_rate=D(str(internal)), is_disbursement=disbursement)


def _fee(desc, rate, qty, when=date(2026, 7, 9), client='Member A'):
    return LegalFeeNote.objects.create(
        date=when, client=client, description=desc, rate=D(str(rate)), qty=D(str(qty)))


class QuarterTests(TestCase):
    """Calendar quarters — the basis the CFO fixed for the member benefit year.
    A bonus measured over a window the contract does not use is unenforceable."""

    def test_quarters_are_calendar_quarters(self):
        self.assertEqual(calc.quarter_key(date(2026, 1, 31)), '2026-Q1')
        self.assertEqual(calc.quarter_key(date(2026, 3, 31)), '2026-Q1')
        self.assertEqual(calc.quarter_key(date(2026, 4, 1)), '2026-Q2')
        self.assertEqual(calc.quarter_key(date(2026, 12, 31)), '2026-Q4')

    def test_a_quarter_names_its_three_months_in_order(self):
        self.assertEqual(calc.quarter_months('2026-Q3'), ['2026-07', '2026-08', '2026-09'])
        self.assertEqual(calc.quarter_months('2026-Q4'), ['2026-10', '2026-11', '2026-12'])

    def test_a_month_knows_its_quarter(self):
        self.assertEqual(calc.quarter_of_month('2026-07'), '2026-Q3')
        self.assertEqual(calc.quarter_of_month('2026-01'), '2026-Q1')


class ComparisonTests(TestCase):
    """Savings vs external attorney — ONE fixed panel rate for every TASK, and
    disbursements compared at their own cost so they carry no saving.

    Reported 17-Sep-2026: the tracker read a per-task rate from the map, so tasks
    were compared against different rates and an unmapped task showed "no mapping"
    and dropped out. CFO decision: every task is a flat BWP 1,900; out-of-pocket
    disbursements are passed through at cost (no invented saving)."""

    FIXED = D('1900')

    def setUp(self):
        self.settings = LegalSettings.solo()

    def test_every_task_compares_against_the_one_fixed_panel_rate(self):
        """Mapped flat, mapped per-hour, or not mapped at all — a TASK is always
        the same 1,900, never a per-task rate or a multiple of the hours."""
        _mapping('Initial consultation', 'flat', 500)
        _mapping('Legal research', 'per_hour', 2250)
        index = calc.mapping_index(LegalRateMapping.objects.all())
        for desc, rate, qty in [('Initial consultation', 200, 3),
                                ('Legal research', 300, '0.75'),
                                ('Something nobody mapped', 300, 2)]:
            self.assertEqual(calc.external_equivalent(_fee(desc, rate, qty), index), self.FIXED)

    def test_a_disbursement_is_compared_at_cost_and_carries_no_saving(self):
        """A courier or a filing fee costs the same in-house or externally, so it
        must never show a saving — comparing it to 1,900 would inflate the bonus."""
        _mapping('Courier', 'flat', 120, disbursement=True)
        f = _fee('Courier', 120, 1)                      # internal 120
        index = calc.mapping_index(LegalRateMapping.objects.all())
        self.assertEqual(calc.external_equivalent(f, index), D('120'))
        line = calc.fee_line(f, index)
        self.assertTrue(line['is_disbursement'])
        self.assertEqual(line['saving'], '0')

    def test_an_unmapped_task_is_still_compared_not_left_out(self):
        f = _fee('Something nobody mapped', 300, 2)      # internal 600
        index = calc.mapping_index(LegalRateMapping.objects.all())
        line = calc.fee_line(f, index)
        self.assertTrue(line['mapped'])
        self.assertEqual(line['external_equivalent'], '1900')
        self.assertEqual(line['saving'], str(self.FIXED - D('600')))

    def test_totals_saving_comes_from_tasks_not_disbursements(self):
        _mapping('Courier', 'flat', 120, disbursement=True)
        task = _fee('Legal research', 300, 1)            # internal 300, external 1900
        _fee('Courier', 120, 1)                          # internal 120, external 120, saving 0
        index = calc.mapping_index(LegalRateMapping.objects.all())
        t = calc.totals_for_fees(list(LegalFeeNote.objects.all()), index)
        self.assertEqual(t['internal'], D('420'))        # 300 + 120
        self.assertEqual(t['external'], D('2020'))       # 1900 + 120
        self.assertEqual(t['saving'], D('1600'))         # only the task saves
        self.assertEqual(t['unmapped'], 0)


class SlaTests(TestCase):
    def test_a_bill_with_no_arrival_date_counts_on_neither_side(self):
        """An unmeasurable turnaround is not a met SLA — and calling it missed
        would be just as wrong."""
        LegalInvoiceSaving.objects.create(
            date_reviewed=date(2026, 7, 10), invoice_ref='A',
            original_amount=D('100'), agreed_amount=D('80'))
        LegalInvoiceSaving.objects.create(
            date_received=date(2026, 7, 1), date_reviewed=date(2026, 7, 3),
            invoice_ref='B', original_amount=D('100'), agreed_amount=D('90'))
        s = calc.sla_summary(list(LegalInvoiceSaving.objects.all()), 5)
        self.assertEqual((s['measured'], s['within'], s['unknown']), (1, 1, 1))

    def test_a_bill_turned_round_on_the_target_day_is_met_not_missed(self):
        r = LegalInvoiceSaving.objects.create(
            date_received=date(2026, 7, 1), date_reviewed=date(2026, 7, 6),
            invoice_ref='C', original_amount=D('100'), agreed_amount=D('50'))
        self.assertEqual(r.turnaround_days, 5)
        self.assertEqual(calc.sla_summary([r], 5)['within'], 1)


class BonusTests(TestCase):
    def setUp(self):
        self.settings = LegalSettings.solo()

    def test_nothing_is_payable_below_the_quarterly_trigger(self):
        r = calc.quarterly_bonus(D('49999.99'), self.settings)
        self.assertFalse(r['met'])
        self.assertEqual(r['bonus'], D('0'))

    def test_at_the_trigger_the_bonus_is_two_percent_of_the_whole_quarter(self):
        """Not 2% of each invoice, and not 2% of the excess over the trigger."""
        r = calc.quarterly_bonus(D('50000'), self.settings)
        self.assertTrue(r['met'])
        self.assertEqual(r['bonus'], D('1000.00'))

    def test_the_monthly_bonus_is_held_to_the_cap(self):
        self.assertEqual(calc.monthly_bonus(D('9000'), self.settings), D('6500'))
        self.assertEqual(calc.monthly_bonus(D('4000'), self.settings), D('4000'))

    def test_a_month_with_no_figure_entered_counts_as_nothing(self):
        self.assertEqual(calc.monthly_bonus(None, self.settings), D('0'))


class ApiTests(TestCase):
    def setUp(self):
        Company.objects.filter(code='ADIC').first() or Company.objects.create(code='ADIC', name='ADIC')
        self.user = User.objects.create_user('bonulegal', email='legal.officer@example.test', password='x')
        UserProfile.objects.update_or_create(
            user=self.user, defaults={'title': UserProfile.Title.ACCOUNTANT, 'is_active': True})
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        LegalSettings.solo()

    # ---- the guards -------------------------------------------------------

    def test_a_typo_in_an_amount_is_refused_not_read_as_zero(self):
        """One parser for the whole module. A rate silently read as 0 is how a
        fee note comes to disagree with its own total."""
        r = self.client.post('/api/v1/bonu/legal/fee-notes/', {
            'date': '2026-07-09', 'client': 'Member A', 'description': 'Time based',
            'rate': 'P15k', 'qty': '1'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('amount', r.json()['detail'].lower())
        self.assertEqual(LegalFeeNote.objects.count(), 0)

    def test_a_fee_line_with_no_client_is_refused(self):
        r = self.client.post('/api/v1/bonu/legal/fee-notes/', {
            'date': '2026-07-09', 'client': '  ', 'description': 'Time based',
            'rate': '35', 'qty': '2'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_a_bill_cannot_be_reviewed_before_it_arrived(self):
        r = self.client.post('/api/v1/bonu/legal/invoice-savings/', {
            'date_received': '2026-07-10', 'date_reviewed': '2026-07-01',
            'invoice_ref': 'X-1', 'original_amount': '1000', 'agreed_amount': '800'},
            format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(LegalInvoiceSaving.objects.count(), 0)

    def test_the_same_service_cannot_be_mapped_twice(self):
        """Two mappings for one service would make the comparison depend on row
        order, so the second is refused rather than silently shadowed."""
        first = self.client.post('/api/v1/bonu/legal/mappings/', {
            'fee_description': 'Legal research', 'external_rate': '2250',
            'calc_basis': 'per_hour'}, format='json')
        self.assertEqual(first.status_code, 201)
        again = self.client.post('/api/v1/bonu/legal/mappings/', {
            'fee_description': 'Legal research', 'external_rate': '99',
            'calc_basis': 'flat'}, format='json')
        self.assertEqual(again.status_code, 409)

    def test_a_negative_monthly_bonus_is_refused(self):
        r = self.client.put('/api/v1/bonu/legal/monthly-bonus/',
                            {'month': '2026-07', 'amount': '-5'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_a_month_must_look_like_a_month(self):
        r = self.client.put('/api/v1/bonu/legal/monthly-bonus/',
                            {'month': 'July', 'amount': '100'}, format='json')
        self.assertEqual(r.status_code, 400)

    # ---- the behaviour ----------------------------------------------------

    def test_the_monthly_bonus_is_recorded_and_reported_at_the_cap(self):
        r = self.client.put('/api/v1/bonu/legal/monthly-bonus/',
                            {'month': '2026-07', 'amount': '9000'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['capped'])
        self.assertEqual(D(r.json()['applied']), D('6500'))
        # The entered figure is kept as entered — the cap is applied on the way
        # out, so nobody's typed figure is quietly rewritten.
        self.assertEqual(LegalMonthlyBonus.objects.get(month='2026-07').amount, D('9000'))

    def test_clearing_the_monthly_bonus_removes_it(self):
        self.client.put('/api/v1/bonu/legal/monthly-bonus/',
                        {'month': '2026-07', 'amount': '1000'}, format='json')
        self.client.put('/api/v1/bonu/legal/monthly-bonus/',
                        {'month': '2026-07', 'amount': ''}, format='json')
        self.assertEqual(LegalMonthlyBonus.objects.filter(month='2026-07').count(), 0)

    def test_changing_the_standard_rate_moves_hourly_comparisons_but_not_disbursements(self):
        hourly = _mapping('Legal research', 'per_hour', 2250)
        phone = _mapping('Called Defendant', 'per_hour', 120, disbursement=True)
        flat = _mapping('Initial consultation', 'flat', 500)
        r = self.client.put('/api/v1/bonu/legal/settings/',
                            {'external_hourly_rate': '2500'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['mappings_repriced'], 1)
        hourly.refresh_from_db(); phone.refresh_from_db(); flat.refresh_from_db()
        self.assertEqual(hourly.external_rate, D('2500'))
        self.assertEqual(phone.external_rate, D('120'), 'a disbursement keeps its own rate')
        self.assertEqual(flat.external_rate, D('500'), 'a flat fee is not an hourly rate')

    def test_an_sla_target_of_zero_days_is_refused(self):
        r = self.client.put('/api/v1/bonu/legal/settings/', {'sla_days': '0'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_it_opens_on_the_last_month_with_work_in_it_not_on_today(self):
        """Work is written up after the month it belongs to. Opening on the
        current month shows a screen of zeros, and zeros read as "nothing was
        done" rather than "not captured yet"."""
        _mapping('Legal research', 'per_hour', 2250)
        _fee('Legal research', 300, 1, when=date(2026, 6, 15))
        body = self.client.get('/api/v1/bonu/legal/').json()
        self.assertEqual(body['month'], '2026-06')
        self.assertEqual(D(body['summary']['matter_billing']), D('300'))
        self.assertIn(calc.month_key(date.today()), body['months'],
                      'the current month must still be selectable')

    def test_with_nothing_captured_it_opens_on_the_current_month(self):
        body = self.client.get('/api/v1/bonu/legal/').json()
        self.assertEqual(body['month'], calc.month_key(date.today()))

    def test_the_overview_reports_the_month_asked_for_not_the_latest(self):
        _mapping('Legal research', 'per_hour', 2250)
        _fee('Legal research', 300, 1, when=date(2026, 6, 15))
        _fee('Legal research', 300, 2, when=date(2026, 7, 15))
        r = self.client.get('/api/v1/bonu/legal/?month=2026-06')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['month'], '2026-06')
        self.assertEqual(D(body['summary']['matter_billing']), D('300'))
        self.assertEqual(D(body['summary']['external_equivalent']), D('1900'))  # one task at the flat rate

    def test_the_quarter_total_adds_the_three_months_and_triggers_the_bonus(self):
        for day, orig in ((date(2026, 7, 5), '30000'), (date(2026, 8, 5), '25000'),
                          (date(2026, 9, 5), '10000')):
            LegalInvoiceSaving.objects.create(
                date_received=day, date_reviewed=day, invoice_ref=f'INV-{day}',
                original_amount=D(orig), agreed_amount=D('0'))
        # A saving in the NEXT quarter must not leak into this one.
        LegalInvoiceSaving.objects.create(
            date_received=date(2026, 10, 5), date_reviewed=date(2026, 10, 5),
            invoice_ref='INV-Q4', original_amount=D('99999'), agreed_amount=D('0'))
        body = self.client.get('/api/v1/bonu/legal/?month=2026-07').json()['summary']
        self.assertEqual(D(body['quarter_saving']), D('65000'))
        self.assertTrue(body['quarter_met'])
        self.assertEqual(D(body['quarter_bonus']), D('1300'))

    def test_advisory_hours_are_valued_at_the_standard_external_rate(self):
        LegalAdvisoryEntry.objects.create(
            date=date(2026, 7, 4), department='Claims', description='NDA review', hours=D('2'))
        body = self.client.get('/api/v1/bonu/legal/?month=2026-07').json()['summary']
        self.assertEqual(D(body['advisory_hours']), D('2'))
        self.assertEqual(D(body['advisory_value']), D('4500'))

    def test_total_value_delivered_adds_the_three_parts(self):
        _fee('Legal research', 300, 1, when=date(2026, 7, 3))          # task: in-house saving 1900−300=1600
        LegalInvoiceSaving.objects.create(
            date_received=date(2026, 7, 2), date_reviewed=date(2026, 7, 4),
            invoice_ref='V-1', original_amount=D('5000'), agreed_amount=D('3000'))  # 2000
        LegalAdvisoryEntry.objects.create(
            date=date(2026, 7, 4), department='Claims', description='NDA', hours=D('1'))  # 2250
        body = self.client.get('/api/v1/bonu/legal/?month=2026-07').json()['summary']
        self.assertEqual(D(body['total_value']), D('5850'))          # 1600 + 2000 + 2250

    def test_a_patch_that_sends_one_field_does_not_blank_the_rest(self):
        _mapping('Legal research', 'per_hour', 2250)
        f = _fee('Legal research', 300, 1, client='Member A')
        r = self.client.patch(f'/api/v1/bonu/legal/fee-notes/{f.id}/', {'qty': '3'}, format='json')
        self.assertEqual(r.status_code, 200)
        f.refresh_from_db()
        self.assertEqual(f.qty, D('3'))
        self.assertEqual(f.client, 'Member A')
        self.assertEqual(f.rate, D('300'))

    def test_a_saved_line_records_who_saved_it(self):
        f = self.client.post('/api/v1/bonu/legal/fee-notes/', {
            'date': '2026-07-09', 'client': 'Member A', 'description': 'Time based',
            'rate': '35', 'qty': '2'}, format='json')
        self.assertEqual(f.status_code, 201)
        self.assertEqual(LegalFeeNote.objects.get().updated_by_email, 'legal.officer@example.test')

    def test_a_line_can_be_removed(self):
        f = _fee('Time based', 35, 2)
        r = self.client.delete(f'/api/v1/bonu/legal/fee-notes/{f.id}/')
        self.assertEqual(r.status_code, 204)
        self.assertEqual(LegalFeeNote.objects.count(), 0)

    def test_section_3_uses_the_demo_basis_the_report_downloads(self):
        """The dashboard's section 3 shows the office demo's figures: internal
        amount billed (ALL lines), external equivalent (mapped), and saving =
        external − ALL billing. That is the SAME basis the Monthly Fee Note
        document uses, so the number on screen never disagrees with the file it
        sits above (H74, 20 Aug 2026). The separate per-matter 'vs external' tab
        keeps its own mapped-line lifetime figures."""
        _fee('Legal research', 300, 1, when=date(2026, 7, 3))     # task: internal 300, external 1900
        _fee('Unmapped thing', 1000, 1, when=date(2026, 7, 3))    # task: internal 1000, external 1900
        s = self.client.get('/api/v1/bonu/legal/?month=2026-07').json()['summary']
        self.assertEqual(D(s['matter_billing']), D('1300'), 'billing counts every line')
        self.assertEqual(D(s['external_equivalent']), D('3800'))   # 2 tasks × 1900
        self.assertEqual(
            D(s['external_equivalent']) - D(s['matter_billing']),
            D(s['in_house_saving']), 'saving = external − ALL billing')
        self.assertEqual(D(s['in_house_saving']), D('2500'))        # 3800 − 1300
        self.assertEqual(D(s['lifetime_mapped_internal']), D('1300'), 'every line is compared now')
        self.assertEqual(
            D(s['lifetime_external']) - D(s['lifetime_mapped_internal']),
            D(s['lifetime_saving']))

    def test_a_fifth_quarter_is_refused_not_shown_as_an_empty_period(self):
        r = self.client.get('/api/v1/bonu/legal/?month=2026-07&quarter=2026-Q5')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['quarter'], '2026-Q3')
        self.assertNotIn('2026-Q5', r.json()['quarters'])

    def test_clearing_a_month_leaves_an_audit_row(self):
        """Somebody will one day ask who removed a bonus figure. A queryset
        delete skips the audit entirely."""
        from core.models import AuditLog
        self.client.put('/api/v1/bonu/legal/monthly-bonus/',
                        {'month': '2026-07', 'amount': '1000'}, format='json')
        before = AuditLog.objects.filter(action='delete').count()
        self.client.put('/api/v1/bonu/legal/monthly-bonus/',
                        {'month': '2026-07', 'amount': ''}, format='json')
        self.assertEqual(AuditLog.objects.filter(action='delete').count(), before + 1)

    def test_repricing_records_who_repriced(self):
        from core.models import AuditLog
        _mapping('Legal research', 'per_hour', 2250)
        self.client.put('/api/v1/bonu/legal/settings/',
                        {'external_hourly_rate': '2500'}, format='json')
        row = AuditLog.objects.filter(table_name='LegalRateMapping').order_by('-created_at').first()
        self.assertIsNotNone(row, 'the repricing must be audited')
        self.assertIsNotNone(row.user_id, 'and it must name who did it')

    def test_a_nonsense_period_in_the_address_falls_back_it_does_not_break(self):
        """A period typed into the address bar is a typo, not a reason to show
        somebody a broken screen."""
        r = self.client.get('/api/v1/bonu/legal/?month=July&quarter=banana')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertRegex(body['month'], r'^\d{4}-\d{2}$')
        self.assertRegex(body['quarter'], r'^\d{4}-Q[1-4]$')

    def test_an_unknown_register_is_a_404_not_a_silent_success(self):
        r = self.client.post('/api/v1/bonu/legal/nonsense/', {'x': 1}, format='json')
        self.assertEqual(r.status_code, 404)


class AccessTests(TestCase):
    def test_a_non_finance_user_cannot_read_or_write_the_legal_office(self):
        u = User.objects.create_user('outsider', email='outsider@example.test', password='x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': UserProfile.Title.OPERATIONS, 'is_active': True})
        c = APIClient()
        c.force_authenticate(User.objects.get(pk=u.pk))
        self.assertEqual(c.get('/api/v1/bonu/legal/').status_code, 403)
        self.assertEqual(c.post('/api/v1/bonu/legal/fee-notes/', {
            'date': '2026-07-09', 'client': 'A', 'description': 'B',
            'rate': '1', 'qty': '1'}, format='json').status_code, 403)
        self.assertEqual(LegalFeeNote.objects.count(), 0)


class SeedTests(TestCase):
    def test_seeding_twice_does_not_duplicate_the_rate_cards(self):
        from django.core.management import call_command
        call_command('bonu_seed_legal_tariff')
        first = (LegalTariffItem.objects.count(), LegalRateMapping.objects.count())
        call_command('bonu_seed_legal_tariff')
        self.assertEqual((LegalTariffItem.objects.count(), LegalRateMapping.objects.count()), first)
        self.assertGreater(first[0], 0)

    def test_a_rate_corrected_on_screen_survives_a_re_seed(self):
        from django.core.management import call_command
        call_command('bonu_seed_legal_tariff')
        m = LegalRateMapping.objects.get(fee_description='Legal research')
        m.external_rate = D('3000'); m.save()
        call_command('bonu_seed_legal_tariff')
        m.refresh_from_db()
        self.assertEqual(m.external_rate, D('3000'))
