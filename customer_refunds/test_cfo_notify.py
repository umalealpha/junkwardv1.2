"""customer_refunds/test_cfo_notify.py — the CFO must actually be told.

CFO 2026-07-29, arming the refund money leg: "A, and it should create a
dashboard entry."

Two defects found on that path before arming it:

1. `notify_cfo_to_authorise` raised the OmniTask with assigner == assignee (the
   CFO tasks himself) and then sent it through `taskboard.email_task_assigned`,
   which returns 0 the moment assigner == assignee ("don't email someone a task
   they gave themselves"). So the ONLY alert on a live money path could never
   send. The email is now built here instead.

2. A customer refund loaded in FNB appeared on NO dashboard. The existing
   "refunds" approval stream is STAFF EXPENSE claims (hris.ExpenseClaim) — a
   different thing entirely — and there is no customer-refund page at all, so a
   payment could sit at the bank unnoticed.

Run in CI (needs a DB): manage.py test customer_refunds.test_cfo_notify
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from core.models import OmniTask, UserProfile
from customer_refunds.models import CustomerRefund


class CfoAuthorisationNoticeTests(TestCase):
    def setUp(self):
        self.cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw',
            first_name='Prathap', last_name='Ganesharajah')
        prof, _ = UserProfile.objects.get_or_create(user=self.cfo)
        prof.title = UserProfile.Title.CFO
        prof.is_active = True
        prof.save()
        self.refund = CustomerRefund.objects.create(
            segment=CustomerRefund.Segment.MIS,
            graphite_ref='RFND-000099',
            policy_number='MIS2024079662',
            customer_name='Tiny Maswabi',
            refund_amount=Decimal('474.00'),
            currency='BWP',
            bank_name='FNB Botswana',
            account_last4='6200',
            status=CustomerRefund.Status.FNB_LOADED,
        )

    # ── the email that could never send ─────────────────────────────────────
    def test_live_load_emails_the_cfo(self):
        from customer_refunds.services import notify_cfo_to_authorise
        mail.outbox = []
        notify_cfo_to_authorise(self.refund, {'mode': 'live'}, self.cfo)
        self.assertEqual(len(mail.outbox), 1, 'the CFO was not emailed at all')
        m = mail.outbox[0]
        self.assertEqual(m.to, ['pganesharajah@alphadirect.co.bw'])
        self.assertIn('FNB', m.subject)
        blob = (m.body or '') + ''.join(a[0] for a in m.alternatives)
        self.assertIn('RFND-000099', blob)
        self.assertIn('474.00', blob)
        self.assertIn('MIS2024079662', blob)
        self.assertIn('LIVE', blob)

    def test_the_task_is_still_raised(self):
        from customer_refunds.services import notify_cfo_to_authorise
        notify_cfo_to_authorise(self.refund, {'mode': 'live'}, self.cfo)
        t = OmniTask.objects.filter(source='refund_engine').first()
        self.assertIsNotNone(t)
        self.assertEqual(t.assignee_id, self.cfo.id)

    def test_a_preview_says_no_money_moved(self):
        from customer_refunds.services import notify_cfo_to_authorise
        mail.outbox = []
        notify_cfo_to_authorise(self.refund, {'mode': 'preview'}, self.cfo)
        blob = ''.join(a[0] for a in mail.outbox[0].alternatives)
        self.assertIn('PREVIEW', blob)
        self.assertIn('nothing is loaded at the bank', blob)
        self.assertNotIn('LIVE', blob)

    def test_the_full_account_number_never_leaves_in_the_email(self):
        self.refund.set_account_number('62912345678')
        self.refund.save()
        from customer_refunds.services import notify_cfo_to_authorise
        mail.outbox = []
        notify_cfo_to_authorise(self.refund, {'mode': 'live'}, self.cfo)
        blob = (mail.outbox[0].body or '') + ''.join(a[0] for a in mail.outbox[0].alternatives)
        self.assertNotIn('62912345678', blob)
        self.assertIn('****', blob)      # last four only

    def test_a_broken_mailer_never_breaks_the_money_leg(self):
        from customer_refunds.services import notify_cfo_to_authorise
        with mock.patch('core.notifications.send_html_with_cfo_cc',
                        side_effect=RuntimeError('SMTP down')):
            notify_cfo_to_authorise(self.refund, {'mode': 'live'}, self.cfo)  # must not raise
        self.assertTrue(OmniTask.objects.filter(source='refund_engine').exists())

    # ── the dashboard entry ─────────────────────────────────────────────────
    def test_loaded_refund_shows_on_the_cfo_dashboard(self):
        from core.approvals_views import pending_approvals_for
        streams = {s['key']: s for s in pending_approvals_for(self.cfo)}
        self.assertIn('customer_refunds_fnb', streams,
                      'a refund sitting in FNB is on no dashboard')
        s = streams['customer_refunds_fnb']
        self.assertEqual(s['count'], 1)
        self.assertIn('FNB', s['label'])

    def test_a_refund_not_yet_loaded_is_not_on_the_dashboard(self):
        # Only FNB_LOADED is the CFO's to act on. One still with finance, or
        # already paid, must not sit in his box.
        from core.approvals_views import pending_approvals_for
        for st in (CustomerRefund.Status.FINANCE_QUEUE, CustomerRefund.Status.APPROVED,
                   CustomerRefund.Status.PAID, CustomerRefund.Status.POSTED_BACK,
                   CustomerRefund.Status.REJECTED):
            self.refund.status = st
            self.refund.save(update_fields=['status'])
            keys = {s['key'] for s in pending_approvals_for(self.cfo)}
            self.assertNotIn('customer_refunds_fnb', keys, f'{st} must not be in his box')

    def test_ordinary_staff_do_not_see_the_stream(self):
        # The FNB authorisation is the CFO's alone — nobody else's dashboard.
        from core.approvals_views import pending_approvals_for
        clerk = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        keys = {s['key'] for s in pending_approvals_for(clerk)}
        self.assertNotIn('customer_refunds_fnb', keys)

    def test_it_is_not_confused_with_the_staff_expense_stream(self):
        # The pre-existing "refunds" stream is hris.ExpenseClaim (staff expense
        # claims). Different money, different action — the keys must stay apart.
        from core.approvals_views import pending_approvals_for
        streams = {s['key']: s for s in pending_approvals_for(self.cfo)}
        self.assertIn('customer_refunds_fnb', streams)
        self.assertNotEqual(streams['customer_refunds_fnb']['label'],
                            'Refunds awaiting your approval')


class HandoffRefundSurvivesTheSecondPersonRuleTests(TestCase):
    """CFO 2026-08-20 introduced "whoever creates a payment may not release it".

    That collides with this path on purpose: one resolved CFO user both raises
    the refund payment and sends it, because the two pairs of eyes here are
    Graphite's approval plus the CFO authorising in FNB's own banking (CFO
    2026-07-28), not two Omni users.

    Before this fix the resulting ValidationError escaped stage_handoff_refund,
    so `notify_cfo_to_authorise` never ran and the ingest view's blanket except
    swallowed it: a refund approved, never loaded, and NOBODY TOLD. The refund
    must stage and the CFO must still be emailed, saying it needs a second
    person.
    """

    def setUp(self):
        # A stand-in address, not the real one: this test only counts that an
        # email went out, and a real mailbox in source trips the repo's PII
        # tripwire (correctly - it cannot tell a fixture from a leak).
        self.cfo = User.objects.create_user(
            'pg_handoff', email='cfo.fixture@example.com',
            first_name='Test', last_name='Cfo')
        prof, _ = UserProfile.objects.get_or_create(user=self.cfo)
        prof.title = UserProfile.Title.CFO
        prof.is_active = True
        prof.save()
        self.refund = CustomerRefund.objects.create(
            segment=CustomerRefund.Segment.MIS,
            graphite_ref='RFND-000100',
            policy_number='MIS2024079663',
            customer_name='Handoff Tester',
            refund_amount=Decimal('120.00'),
            currency='BWP',
            bank_name='FNB Botswana',
            account_last4='6200',
            status=CustomerRefund.Status.RECEIVED,
        )

    def test_a_refused_load_still_stages_and_still_tells_the_cfo(self):
        from django.core.exceptions import ValidationError
        from customer_refunds import services
        mail.outbox = []
        with mock.patch.object(services, 'create_refund_payment',
                               return_value=None), \
             mock.patch.object(
                 services, 'load_refund_to_fnb',
                 side_effect=ValidationError(
                     'You created these payments, so someone else has to '
                     'release them to the bank: PAY-OUT-2026-000123.')):
            out = services.stage_handoff_refund(self.refund)

        self.assertTrue(out['staged'], msg=str(out))
        self.assertEqual(out['load'].get('mode'), 'preview')
        self.assertIn('Not loaded to the bank', out['load'].get('error', ''))
        self.assertEqual(len(mail.outbox), 1,
                         'the CFO was not told the refund could not be loaded')

    def test_a_config_problem_still_behaves_as_before(self):
        """So the new clause has not swallowed the existing one."""
        from customer_refunds import services
        from customer_refunds.services import RefundConfigError
        mail.outbox = []
        with mock.patch.object(services, 'create_refund_payment',
                               return_value=None), \
             mock.patch.object(services, 'load_refund_to_fnb',
                               side_effect=RefundConfigError('no source account')):
            out = services.stage_handoff_refund(self.refund)
        self.assertTrue(out['staged'])
        self.assertEqual(out['load'].get('error'), 'no source account')
        self.assertEqual(len(mail.outbox), 1)


class RefundAutoLoadThresholdTests(TestCase):
    """CFO 2026-08-20: engine refunds auto-load "only below an amount".

    Resolves the collision between his 2026-07-28 ruling (Graphite's approval
    plus his own authorisation in FNB banking ARE the two eyes, single actor in
    Omni) and the 2026-08-20 rule that a creator may not release. Below the
    line the July behaviour stands; above it the refund waits for a second
    person.
    """

    def setUp(self):
        self.cfo = User.objects.create_user(
            'pg_threshold', email='cfo.fixture2@example.com',
            first_name='Test', last_name='Cfo')
        prof, _ = UserProfile.objects.get_or_create(user=self.cfo)
        prof.title = UserProfile.Title.CFO
        prof.is_active = True
        prof.save()

    def _refund(self, amount, ref):
        return CustomerRefund.objects.create(
            segment=CustomerRefund.Segment.MIS, graphite_ref=ref,
            policy_number='MIS2024079999', customer_name='Threshold Tester',
            refund_amount=Decimal(amount), currency='BWP',
            bank_name='FNB Botswana', account_last4='6200',
            status=CustomerRefund.Status.RECEIVED)

    def test_the_default_matches_graphites_own_line(self):
        from customer_refunds.services import refund_autoload_ceiling
        self.assertEqual(refund_autoload_ceiling(), Decimal('5000'))

    def test_a_malformed_setting_stops_every_auto_load(self):
        """Never guess on a money control."""
        from django.test import override_settings
        from customer_refunds.services import refund_autoload_ceiling
        with override_settings(REFUND_AUTOLOAD_MAX_BWP='not a number'):
            self.assertEqual(refund_autoload_ceiling(), Decimal('0'))

    def test_below_the_line_it_still_loads_on_one_actor(self):
        from customer_refunds import services
        mail.outbox = []
        r = self._refund('474.00', 'RFND-000201')
        with mock.patch.object(services, 'create_refund_payment', return_value=None), \
             mock.patch.object(services, 'load_refund_to_fnb',
                               return_value={'mode': 'live'}) as load:
            out = services.stage_handoff_refund(r)
        self.assertTrue(out['staged'])
        self.assertEqual(out['load'].get('mode'), 'live')
        # and it declared the single-actor exemption explicitly
        self.assertTrue(load.call_args.kwargs.get('allow_single_person'))
        self.assertEqual(len(mail.outbox), 1)

    def test_above_the_line_it_stages_and_waits_for_a_second_person(self):
        from customer_refunds import services
        mail.outbox = []
        r = self._refund('7500.00', 'RFND-000202')
        with mock.patch.object(services, 'create_refund_payment', return_value=None), \
             mock.patch.object(services, 'load_refund_to_fnb') as load:
            out = services.stage_handoff_refund(r)
        load.assert_not_called()                 # nothing went near the bank
        self.assertTrue(out['staged'])
        self.assertEqual(out['load'].get('mode'), 'preview')
        self.assertIn('second person', out['load'].get('error', ''))
        self.assertEqual(len(mail.outbox), 1,
                         'the CFO must still be told it is waiting')
