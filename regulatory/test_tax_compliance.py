"""
regulatory/test_tax_compliance.py — the statutory tax compliance workflow.

The date tests pin every line of the reporter's worked calendar, because these
dates changed under the 2026 Acts and the page that existed before this had ALL
of them wrong. If someone "tidies" a rule back to the old law, these go red.

The workflow tests pin the two things that are easy to break and expensive to
get wrong:

  * a preparer marking their own filing complete must NOT stop the reminders
  * LATE (internal miss) and BREACH (statutory miss) must stay separate
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from regulatory.models import TaxComplianceTask, TaxObligationOwner
from regulatory.tax_calendar import TaxType, VatCycle, build_calendar
from regulatory.tax_workflow import (
    close_breach,
    generate_tasks,
    mark_preparer_complete,
    run_reminders,
    verify_and_close,
)


# ─────────────────────────────────────────────────────────────────────────────
# The dates
# ─────────────────────────────────────────────────────────────────────────────

class TaxCalendarDateTests(TestCase):
    """Every assertion here is a line of the reporter's worked calendar,
    cross-checked by them against Andersen Tax, KPMG and Payspace."""

    def setUp(self):
        self.cal = build_calendar(date(2026, 1, 1), date(2027, 12, 31))

    def due(self, tax_type, period_label):
        matches = [o for o in self.cal if o.tax_type == tax_type and o.period_label == period_label]
        self.assertEqual(len(matches), 1, f'expected exactly one {tax_type} for {period_label}')
        return matches[0].due_date

    def test_vat_due_on_the_25th_of_the_following_month(self):
        """The 25th, NOT the 28th the tax advisers quote. CFO correction
        2026-09-11 — he is the authority on what Alpha Direct is held to, and
        the 28th would start the preparer three days late every cycle."""
        self.assertEqual(self.due(TaxType.VAT, 'Jan–Feb 2027'), date(2027, 3, 25))
        self.assertEqual(self.due(TaxType.VAT, 'Mar–Apr 2027'), date(2027, 5, 25))
        self.assertEqual(self.due(TaxType.VAT, 'May–Jun 2026'), date(2026, 7, 25))
        self.assertEqual(self.due(TaxType.VAT, 'Jul–Aug 2026'), date(2026, 9, 25))
        self.assertEqual(self.due(TaxType.VAT, 'Sep–Oct 2026'), date(2026, 11, 25))

    def test_vat_november_december_rolls_into_january(self):
        self.assertEqual(self.due(TaxType.VAT, 'Nov–Dec 2026'), date(2027, 1, 25))

    def test_vat_is_paid_ten_days_early(self):
        """The CFO's instruction: pay 10 days in advance. VAT due the 25th means
        the preparer is reminded from the 15th, so the money moves early."""
        targets = {o.key: o.target_date for o in self.cal}
        self.assertEqual(targets['vat:2026-08-31'], date(2026, 9, 15))
        self.assertEqual(targets['vat:2026-10-31'], date(2026, 11, 15))

    def test_paye_and_owht_due_on_the_14th(self):
        # Was: PAYE on the 15th, WHT at the end of the second month after.
        self.assertEqual(self.due(TaxType.PAYE, 'August 2026'), date(2026, 9, 14))
        self.assertEqual(self.due(TaxType.OWHT, 'August 2026'), date(2026, 9, 14))
        self.assertEqual(self.due(TaxType.PAYE, 'December 2026'), date(2027, 1, 14))

    def test_annual_paye_return_28_days_after_year_end(self):
        # 30 June year-end + 28 days = 28 July. Was: 31 October.
        self.assertEqual(self.due(TaxType.PAYE_ANNUAL, 'FY2025/26'), date(2026, 7, 28))

    def test_sat_quarterly_falls_on_the_quarter_end_itself(self):
        # Four instalments, not two, and with no grace period after the quarter.
        q = {o.period_label.split(' (')[0]: o.due_date
             for o in self.cal if o.tax_type == TaxType.SAT_QUARTERLY}
        self.assertEqual(q['Q1 FY2026/27'], date(2026, 9, 30))
        self.assertEqual(q['Q2 FY2026/27'], date(2026, 12, 31))
        self.assertEqual(q['Q3 FY2026/27'], date(2027, 3, 31))
        self.assertEqual(q['Q4 FY2026/27'], date(2027, 6, 30))

    def test_sat_annual_four_months_after_year_end(self):
        self.assertEqual(self.due(TaxType.SAT_ANNUAL, 'FY2025/26'), date(2026, 10, 31))

    def test_target_is_always_exactly_ten_days_before_due(self):
        for ob in self.cal:
            self.assertEqual((ob.due_date - ob.target_date).days, 10, ob.key)

    def test_reminder_dates_match_the_reporters_worked_examples(self):
        targets = {o.key: o.target_date for o in self.cal}
        self.assertEqual(targets['vat:2027-02-28'], date(2027, 3, 15))   # due 25 Mar
        self.assertEqual(targets['vat:2026-06-30'], date(2026, 7, 15))   # due 25 Jul
        self.assertEqual(targets['sat_q:2026-09-30'], date(2026, 9, 20))  # remind 20 Sep
        self.assertEqual(targets['sat_q:2026-12-31'], date(2026, 12, 21))  # remind 21 Dec
        self.assertEqual(targets['sat_annual:2026-06-30'], date(2026, 10, 21))  # remind 21 Oct

    def test_no_duplicate_obligations(self):
        keys = [o.key for o in self.cal]
        self.assertEqual(len(keys), len(set(keys)))

    def test_vat_cycle_is_a_setting_not_a_hardcode(self):
        """Category B is what Alpha Direct is on, but BURS can move us. Flipping
        the setting must move every VAT date, with no code change."""
        monthly = [o for o in build_calendar(date(2026, 7, 1), date(2026, 12, 31),
                                             vat_cycle=VatCycle.MONTHLY)
                   if o.tax_type == TaxType.VAT]
        two_monthly = [o for o in self.cal
                       if o.tax_type == TaxType.VAT
                       and date(2026, 7, 1) <= o.due_date <= date(2026, 12, 31)]
        self.assertGreater(len(monthly), len(two_monthly))
        for o in monthly:
            self.assertEqual(o.due_date.day, 25)

        cat_a = [o for o in build_calendar(date(2026, 1, 1), date(2026, 12, 31),
                                           vat_cycle=VatCycle.CATEGORY_A)
                 if o.tax_type == TaxType.VAT]
        # Category A periods close in odd months, so they are due in even ones.
        self.assertTrue(all(o.period_end.month % 2 == 1 for o in cat_a))


# ─────────────────────────────────────────────────────────────────────────────
# The workflow
# ─────────────────────────────────────────────────────────────────────────────

class TaxWorkflowTests(TestCase):

    def setUp(self):
        self.preparer = User.objects.create_user(
            'preparer', email='preparer@alphadirect.co.bw', password='x')
        self.cfo = User.objects.create_user(
            'cfo', email='cfo@alphadirect.co.bw', password='x', is_superuser=True)

    def make_task(self, *, due, status=TaxComplianceTask.Status.SCHEDULED, **kw):
        return TaxComplianceTask.objects.create(
            obligation_key = kw.pop('key', f'test:{due.isoformat()}'),
            tax_type       = TaxType.VAT,
            period_label   = 'Jul–Aug 2026',
            period_start   = date(2026, 7, 1),
            period_end     = date(2026, 8, 31),
            due_date       = due,
            target_date    = due - timedelta(days=10),
            owner          = self.preparer,
            status         = status,
            **kw,
        )

    # --- generation ---

    def test_generation_is_idempotent(self):
        first  = generate_tasks(months_ahead=12)
        before = TaxComplianceTask.objects.count()
        second = generate_tasks(months_ahead=12)
        self.assertGreater(first['created'], 0)
        self.assertEqual(second['created'], 0)
        self.assertEqual(TaxComplianceTask.objects.count(), before)

    def test_generation_never_resets_work_in_progress(self):
        generate_tasks(months_ahead=12)
        task = TaxComplianceTask.objects.first()
        task.status = TaxComplianceTask.Status.PREPARER_COMPLETE
        task.completion_note = 'filed, ref 12345'
        task.save()

        generate_tasks(months_ahead=12)

        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.PREPARER_COMPLETE)
        self.assertEqual(task.completion_note, 'filed, ref 12345')

    def test_generated_tasks_pick_up_the_configured_owner(self):
        TaxObligationOwner.objects.create(tax_type=TaxType.VAT, owner=self.preparer)
        generate_tasks(months_ahead=12)
        vat = TaxComplianceTask.objects.filter(tax_type=TaxType.VAT).first()
        self.assertEqual(vat.owner, self.preparer)

    # --- the state machine ---

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_task_activates_exactly_on_the_target_date(self, _mail):
        task = self.make_task(due=date(2026, 9, 28))

        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 17)):
            run_reminders()
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.SCHEDULED)

        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 18)):
            run_reminders()
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.REMINDING)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_preparer_complete_does_NOT_stop_the_reminders(self, mail):
        """The single most important behaviour in this module.

        If marking your own filing complete silenced the reminder, the CFO
        verification step would be optional in practice and the control would be
        decorative. The reminder runs until it is VERIFIED.
        """
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        mark_preparer_complete(task, self.preparer, 'filed on BURS portal')
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.PREPARER_COMPLETE)

        mail.reset_mock()
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 20)):
            stats = run_reminders()

        self.assertEqual(stats['reminders_sent'], 1)
        self.assertTrue(mail.called)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_only_verification_stops_the_reminders(self, mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        mark_preparer_complete(task, self.preparer)
        # Pin the finish BEFORE the 18-Sep target. Left on the real clock this
        # test went red at midnight on 19-Sep-2026 (VERIFIED became LATE) and
        # blocked every PR — the fixed dates above are a calendar time bomb.
        task.completed_at = timezone.make_aware(
            timezone.datetime(2026, 9, 15, 10, 0), timezone.get_current_timezone())
        task.save(update_fields=['completed_at'])
        verify_and_close(task, self.cfo, note='checked against the BURS receipt')
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.VERIFIED)
        self.assertTrue(task.is_closed)

        mail.reset_mock()
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 21)):
            stats = run_reminders()
        self.assertEqual(stats['reminders_sent'], 0)
        self.assertFalse(mail.called)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_one_reminder_per_day_even_if_the_cron_runs_twice(self, mail):
        self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 20)):
            run_reminders()
            second = run_reminders()
        self.assertEqual(second['reminders_sent'], 0)
        self.assertEqual(mail.call_count, 1)

    # --- late vs breach: two different failures ---

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_closing_after_the_target_but_before_the_due_date_is_LATE_not_breach(self, _mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        # Preparer finished on the 24th: past the 18th target, before the 28th.
        task.completed_at = timezone.make_aware(
            timezone.datetime(2026, 9, 24, 10, 0), timezone.get_current_timezone())
        task.save()

        verify_and_close(task, self.cfo, late_reason='waiting on the bank statement')
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.LATE)
        self.assertEqual(task.late_reason, 'waiting on the bank statement')
        self.assertEqual(task.breach_note, '')  # a late close is NOT a breach

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_filing_on_the_due_date_itself_is_on_time(self, _mail):
        """`>` not `>=`. Filing on the 28th is compliant; a sweep on the 28th
        must not declare a breach."""
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 28)):
            run_reminders()
        task.refresh_from_db()
        self.assertNotEqual(task.status, TaxComplianceTask.Status.BREACH)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_passing_the_due_date_unverified_is_a_breach(self, mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 29)):
            stats = run_reminders()
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.BREACH)
        self.assertEqual(stats['breach_alerts'], 1)
        self.assertIsNotNone(task.breach_flagged_at)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_preparer_marked_done_still_breaches_if_never_verified(self, _mail):
        """The trap: the preparer filed it, so everyone relaxed — but nobody
        verified, so as far as the control is concerned the deadline was missed."""
        task = self.make_task(due=date(2026, 9, 28),
                              status=TaxComplianceTask.Status.PREPARER_COMPLETE)
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 30)):
            run_reminders()
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.BREACH)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_breach_alert_is_sent_once_not_every_day(self, mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 29)):
            first = run_reminders()
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 30)):
            second = run_reminders()
        self.assertEqual(first['breach_alerts'], 1)
        self.assertEqual(second['breach_alerts'], 0)
        # ...but the DAILY reminder keeps going while it is still open.
        self.assertEqual(second['reminders_sent'], 1)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_a_breach_cannot_be_closed_by_ordinary_verification(self, _mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.BREACH)
        with self.assertRaises(ValueError):
            verify_and_close(task, self.cfo)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_a_breach_cannot_be_closed_without_a_written_reason(self, _mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.BREACH)
        with self.assertRaises(ValueError):
            close_breach(task, self.cfo, '   ')
        close_breach(task, self.cfo, 'BURS portal was down; filed 29th, penalty waived.')
        task.refresh_from_db()
        self.assertEqual(task.status, TaxComplianceTask.Status.LATE)
        self.assertIn('BURS portal', task.breach_note)

    # --- escalation ---

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_cfo_is_copied_only_from_three_days_out(self, mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)

        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 20)):
            run_reminders()
        self.assertFalse(mail.call_args.kwargs['cc_cfo'], 'CFO copied 8 days out — too early')

        task.last_reminded_on = None
        task.save()
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 25)):
            run_reminders()
        self.assertTrue(mail.call_args.kwargs['cc_cfo'], 'CFO not copied 3 days out')

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_an_unowned_obligation_is_counted_not_silently_dropped(self, mail):
        task = self.make_task(due=date(2026, 9, 28), status=TaxComplianceTask.Status.REMINDING)
        task.owner = None
        task.save()
        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 20)):
            stats = run_reminders()
        self.assertEqual(stats['skipped_no_owner'], 1)
        self.assertEqual(stats['reminders_sent'], 0)
        self.assertFalse(mail.called)


class TaxComplianceApiTests(TestCase):

    def setUp(self):
        self.preparer = User.objects.create_user(
            'prep2', email='prep2@alphadirect.co.bw', password='x')
        self.other = User.objects.create_user(
            'other', email='other@alphadirect.co.bw', password='x')
        self.cfo = User.objects.create_user(
            'cfo2', email='cfo2@alphadirect.co.bw', password='x', is_superuser=True)
        self.task = TaxComplianceTask.objects.create(
            obligation_key='api:1', tax_type=TaxType.VAT, period_label='Jul–Aug 2026',
            period_start=date(2026, 7, 1), period_end=date(2026, 8, 31),
            due_date=date(2026, 9, 28), target_date=date(2026, 9, 18),
            owner=self.preparer, status=TaxComplianceTask.Status.REMINDING,
        )

    def test_calendar_requires_login(self):
        self.assertIn(self.client.get('/api/v1/tax-compliance/calendar/').status_code, (401, 403))

    def test_calendar_lists_tasks(self):
        self.client.force_login(self.preparer)
        res = self.client.get('/api/v1/tax-compliance/calendar/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['summary']['open'], 1)

    def test_a_preparer_cannot_verify_their_own_filing(self):
        """The control. If this ever returns 200, the whole chain is worthless."""
        self.client.force_login(self.preparer)
        res = self.client.post(f'/api/v1/tax-compliance/tasks/{self.task.id}/verify/')
        self.assertEqual(res.status_code, 403)

    def test_someone_elses_filing_cannot_be_marked_complete(self):
        self.client.force_login(self.other)
        res = self.client.post(f'/api/v1/tax-compliance/tasks/{self.task.id}/complete/')
        self.assertEqual(res.status_code, 403)

    def test_owner_can_mark_complete_and_cfo_can_verify(self):
        self.client.force_login(self.preparer)
        res = self.client.post(
            f'/api/v1/tax-compliance/tasks/{self.task.id}/complete/',
            {'note': 'filed'}, content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], TaxComplianceTask.Status.PREPARER_COMPLETE)

        self.client.force_login(self.cfo)
        res = self.client.post(f'/api/v1/tax-compliance/tasks/{self.task.id}/verify/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(res.json()['status'],
                      (TaxComplianceTask.Status.VERIFIED, TaxComplianceTask.Status.LATE))

    def test_an_APPROVER_still_cannot_verify_their_own_filing(self):
        """The hole the first version had, found by checking the LIVE accounts.

        Both real preparers — Pako Kago and Kago Tshutlhedi — are approvers in
        Omni. So an approver-only check let each of them mark their own filing
        complete and then verify it, making the CFO sign-off self-service. The
        original test missed it purely because its preparer was NOT an approver.

        Separation of duties: the person who prepared it cannot be the person who
        verifies it, however senior they are.
        """
        from core.models import UserProfile, get_user_profile

        approver_preparer = User.objects.create_user(
            'fc', email='fc@alphadirect.co.bw', password='x')
        # `can_approve_journal_entries` is a read-only PROPERTY driven by the job
        # TITLE, not a flag — an earlier version of this test set the property,
        # which did nothing, so the test passed for the wrong reason (it got its
        # 403 from the approver check, not the separation check). Set the title.
        UserProfile.objects.update_or_create(
            user=approver_preparer,
            defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER, 'is_active': True},
        )
        approver_preparer.refresh_from_db()
        self.assertTrue(
            get_user_profile(approver_preparer).can_approve_journal_entries,
            'test setup failed: this user is not actually an approver, so the '
            'test would pass without the separation-of-duties check',
        )

        self.task.owner = approver_preparer
        self.task.save()

        self.client.force_login(approver_preparer)
        self.client.post(f'/api/v1/tax-compliance/tasks/{self.task.id}/complete/',
                         {'note': 'filed'}, content_type='application/json')
        res = self.client.post(f'/api/v1/tax-compliance/tasks/{self.task.id}/verify/')
        self.assertEqual(res.status_code, 403, 'an approver verified their OWN filing')

        # ...and someone else with the same rights still can.
        self.client.force_login(self.cfo)
        res = self.client.post(f'/api/v1/tax-compliance/tasks/{self.task.id}/verify/')
        self.assertEqual(res.status_code, 200)

    def test_reassigning_an_owner_moves_open_tasks_only(self):
        closed = TaxComplianceTask.objects.create(
            obligation_key='api:2', tax_type=TaxType.VAT, period_label='May–Jun 2026',
            period_start=date(2026, 5, 1), period_end=date(2026, 6, 30),
            due_date=date(2026, 7, 28), target_date=date(2026, 7, 18),
            owner=self.preparer, status=TaxComplianceTask.Status.VERIFIED,
        )
        self.client.force_login(self.cfo)
        res = self.client.post('/api/v1/tax-compliance/owners/',
                               {'tax_type': TaxType.VAT, 'owner_id': self.other.id},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)

        self.task.refresh_from_db()
        closed.refresh_from_db()
        self.assertEqual(self.task.owner, self.other)      # open task moves
        self.assertEqual(closed.owner, self.preparer)      # history does not


# ─────────────────────────────────────────────────────────────────────────────
# Changing a statutory date (CFO instruction 2026-09-11)
# ─────────────────────────────────────────────────────────────────────────────

class TaxDateChangeTests(TestCase):
    """Oprah, Kago and Legakwa can move a statutory date. Two of the three also
    PREPARE filings, so these tests exist to prove the permission cannot be used
    to hide a miss."""

    def setUp(self):
        from regulatory.models import TaxCalendarEditor
        self.editor = User.objects.create_user(
            'oprah', email='omogomotsi@alphadirect.co.bw', password='x',
            first_name='Oprah', last_name='Mogomotsi')
        TaxCalendarEditor.objects.create(user=self.editor, note='raised the requirement')
        # Deliberately has NO title and NO list row — the true outsider.
        self.outsider = User.objects.create_user(
            'nobody', email='nobody@alphadirect.co.bw', password='x')
        self.cfo = User.objects.create_user(
            'cfo3', email='cfo3@alphadirect.co.bw', password='x', is_superuser=True)
        # The preparer is someone OTHER than the editor by default, so these
        # tests exercise the ordinary path. The tests that care about a preparer
        # editing their own filing set `task.owner = self.editor` themselves —
        # keeping that out of the shared fixture is what stops "can an editor
        # move a date at all?" and "can a preparer extend their own deadline?"
        # from silently becoming the same test.
        self.preparer = User.objects.create_user(
            'prep_dc', email='prep_dc@alphadirect.co.bw', password='x')
        self.task = TaxComplianceTask.objects.create(
            obligation_key='dc:1', tax_type=TaxType.VAT, period_label='Jul–Aug 2026',
            period_start=date(2026, 7, 1), period_end=date(2026, 8, 31),
            due_date=date(2026, 9, 25), target_date=date(2026, 9, 15),
            owner=self.preparer, status=TaxComplianceTask.Status.REMINDING,
        )

    def post_change(self, user, due, reason='BURS extension'):
        self.client.force_login(user)
        return self.client.post(
            f'/api/v1/tax-compliance/tasks/{self.task.id}/change-date/',
            {'due_date': due, 'reason': reason}, content_type='application/json')

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_a_named_editor_can_move_a_date(self, _mail):
        res = self.post_change(self.editor, '2026-09-30')
        self.assertEqual(res.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date, date(2026, 9, 30))
        # the prepare-by date follows it, still 10 days ahead
        self.assertEqual(self.task.target_date, date(2026, 9, 20))

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_someone_without_the_right_cannot(self, _mail):
        self.assertEqual(self.post_change(self.outsider, '2026-09-30').status_code, 403)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_the_original_computed_date_is_kept_forever(self, _mail):
        self.post_change(self.editor, '2026-09-30')
        self.post_change(self.editor, '2026-10-05', reason='second move')
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date, date(2026, 10, 5))
        # NOT 30 Sep — the first change must not be overwritten by the second,
        # or the rules-computed date is lost after two edits.
        self.assertEqual(self.task.original_due_date, date(2026, 9, 25))

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_a_reason_is_mandatory(self, _mail):
        res = self.post_change(self.editor, '2026-09-30', reason='   ')
        self.assertEqual(res.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date, date(2026, 9, 25))

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_MOVING_THE_DATE_CANNOT_UNDO_A_BREACH(self, _mail):
        """The one that matters.

        Kago and Legakwa can both edit dates AND prepare filings. Without this,
        a missed PAYE deadline could be made to look on time by dragging the
        date forward — and the breach flag, the CFO alert and the compliance
        record would all be worthless.
        """
        self.task.status = TaxComplianceTask.Status.BREACH
        self.task.save()

        res = self.post_change(self.editor, '2026-12-31', reason='give us more time')
        self.assertEqual(res.status_code, 400)

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaxComplianceTask.Status.BREACH)
        self.assertEqual(self.task.due_date, date(2026, 9, 25))

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_a_closed_filing_cannot_be_back_dated(self, _mail):
        self.task.status = TaxComplianceTask.Status.VERIFIED
        self.task.save()
        self.assertEqual(self.post_change(self.editor, '2026-10-30').status_code, 400)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_the_cfo_is_told_about_every_change(self, mail):
        self.post_change(self.editor, '2026-09-30')
        self.assertTrue(mail.called)
        kw = mail.call_args.kwargs
        self.assertTrue(kw['cc_cfo'])
        self.assertIn('Tax date changed', kw['subject'])

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_moving_a_date_closer_opens_the_reminder_window(self, _mail):
        """Pull the deadline forward into the next 10 days and it must start
        reminding, not sit in 'scheduled' until the old target passed."""
        self.task.status = TaxComplianceTask.Status.SCHEDULED
        self.task.due_date = date(2026, 12, 25)
        self.task.target_date = date(2026, 12, 15)
        self.task.save()

        with patch('regulatory.tax_workflow.today_gabs', return_value=date(2026, 9, 20)):
            self.post_change(self.editor, '2026-09-25', reason='brought forward')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaxComplianceTask.Status.REMINDING)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_A_PREPARER_CANNOT_PUSH_THEIR_OWN_DEADLINE_LATER(self, _mail):
        """The hole Fable found, and the one that actually mattered.

        The breach guard only refuses when the task is ALREADY in breach — and
        breach is raised by the nightly sweep on `today > due_date`. So the
        preparer had the whole due day, plus until 06:30 the next morning, to
        push their own deadline out. No breach, no CFO alert, a green board, and
        the record closing as VERIFIED and on time. The guard would never have
        fired for anyone who understood the schedule.

        Guard the TRANSITION, not the state.
        """
        self.task.owner = self.editor          # the editor is also the preparer
        self.task.save()

        res = self.post_change(self.editor, '2026-10-31', reason='need more time')
        self.assertEqual(res.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.due_date, date(2026, 9, 25))

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_a_preparer_may_still_pull_their_own_deadline_EARLIER(self, _mail):
        """Only pushing out is refused. Bringing a filing forward is never a way
        to hide a miss, and blocking it would be pointless friction."""
        self.task.owner = self.editor
        self.task.save()
        res = self.post_change(self.editor, '2026-09-20', reason='closing early')
        self.assertEqual(res.status_code, 200)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_someone_else_may_push_that_same_deadline_later(self, _mail):
        """The instruction is narrowed as little as possible: it is only YOUR OWN
        filing you cannot extend. Another editor can still move it."""
        from regulatory.models import TaxCalendarEditor
        other = User.objects.create_user('legakwa', email='lntabeni@alphadirect.co.bw',
                                         password='x', first_name='Legakwa')
        TaxCalendarEditor.objects.create(user=other)
        self.task.owner = self.editor
        self.task.save()
        self.assertEqual(self.post_change(other, '2026-10-31').status_code, 200)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_a_finance_title_ALONE_does_not_grant_the_right(self, _mail):
        """The trap under the trap.

        All three named editors are Finance Managers on prod, so they passed
        through the old title gate while TaxCalendarEditor sat EMPTY. The list
        looked like the control and was granting nothing — and removing someone
        from it would have taken nothing away, silently. The list is now the
        only grant.

        Note the shape of this test: it asserts the user really does hold the
        approving title BEFORE testing, because a fixture that quietly fails to
        grant the role makes the whole test pass for the wrong reason. That
        exact mistake shipped once already on this module.
        """
        from core.models import UserProfile, get_user_profile
        titled = User.objects.create_user('fm2', email='fm2@alphadirect.co.bw', password='x')
        UserProfile.objects.update_or_create(
            user=titled,
            defaults={'title': UserProfile.Title.FINANCE_MANAGER, 'is_active': True},
        )
        titled.refresh_from_db()
        self.assertTrue(
            get_user_profile(titled).can_approve_journal_entries,
            'fixture failed: not actually a title-holder, so this test would '
            'pass without proving anything',
        )

        self.assertEqual(self.post_change(titled, '2026-09-30').status_code, 403)

        from regulatory.models import TaxCalendarEditor
        TaxCalendarEditor.objects.create(user=titled, note='added by the CFO')
        self.assertEqual(self.post_change(titled, '2026-09-30').status_code, 200)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_removing_someone_from_the_list_actually_removes_the_right(self, _mail):
        """The whole point of making the list the only grant."""
        from regulatory.models import TaxCalendarEditor
        self.client.force_login(self.cfo)
        self.client.post('/api/v1/tax-compliance/date-editors/',
                         {'user_id': self.editor.id, 'remove': True},
                         content_type='application/json')
        self.assertFalse(TaxCalendarEditor.objects.filter(user=self.editor).exists())
        self.assertEqual(self.post_change(self.editor, '2026-09-30').status_code, 403)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_an_unknown_user_id_is_refused_not_crashed(self, _mail):
        """Used to reach the database as a bad foreign key and 500. A wrong but
        REAL id would have silently granted the right to somebody nobody chose."""
        self.client.force_login(self.cfo)
        res = self.client.post('/api/v1/tax-compliance/date-editors/',
                               {'user_id': 999999}, content_type='application/json')
        self.assertEqual(res.status_code, 400)

    @patch('regulatory.tax_workflow.send_html_with_cfo_cc')
    def test_only_the_cfo_can_hand_out_the_right(self, _mail):
        self.client.force_login(self.editor)
        res = self.client.post('/api/v1/tax-compliance/date-editors/',
                               {'user_id': self.outsider.id},
                               content_type='application/json')
        self.assertEqual(res.status_code, 403)

        self.client.force_login(self.cfo)
        res = self.client.post('/api/v1/tax-compliance/date-editors/',
                               {'user_id': self.outsider.id, 'note': 'cover'},
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.post_change(self.outsider, '2026-09-30').status_code, 200)
