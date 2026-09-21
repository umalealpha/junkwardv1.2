"""B9 Subrogation — the alert, the demand letter and the 48-hour escalation.

Every test here is written so that it goes RED if its fix is removed. The ones
that matter most are the two guards:

  * `test_escalation_survives_the_record_being_touched_repeatedly` — the clock
    must be anchored to when the flag ARRIVED, not to `updated_at`. Re-point it
    at `updated_at` and this test fails.
  * `test_alert_to_lindani_is_not_copied_to_the_shared_exco_mailbox` — the alert
    carries a one-tap link into her case. Drop `cc_cfo=False` and this fails.

No email leaves the machine: Django's test runner swaps the mail backend for
the in-memory one, and every address used below is checked against
`mail.outbox`, never sent.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from claims.models import Subrogation
from claims.subrogation_alert import (ESCALATION_HOURS, FINANCE_MANAGER_EMAIL,
                                      LINDANI_EMAIL, due_for_escalation,
                                      escalate, flag_recovery_possible,
                                      notify_recovery_flagged,
                                      send_demand_letter)
from claims.subrogation_demand_pdf import build_demand_letter_pdf

CFO_SHARED_MAILBOX = 'excoboard@alphadirect.co.bw'


class SubrogationB9Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='lindani-test',
                                       email=LINDANI_EMAIL, is_active=True)

    def _sub(self, **kw):
        base = dict(
            claim_reference='CLM-2026-000999',
            third_party_name='Test Third Party',   # fictional — no real PII
            third_party_insurer='Test Insurer Ltd',
            claim_paid_amount=Decimal('80000.00'),
            expected_recovery=Decimal('65000.00'),
            created_by=self.user,
        )
        base.update(kw)
        return Subrogation.objects.create(**base)

    # ── The flag ────────────────────────────────────────────────────────────

    def test_flag_stamps_once_and_a_repeat_arrival_is_a_no_op(self):
        """The nightly feed re-sends the same snapshot every night. The second
        arrival must not re-stamp the clock, or a stalled case would be handed
        a fresh 48 hours every single night and never escalate."""
        sub = self._sub()
        self.assertTrue(flag_recovery_possible(sub, source='graphite'))
        first = sub.recovery_flagged_at
        self.assertIsNotNone(first)

        self.assertFalse(flag_recovery_possible(sub, source='graphite'))
        sub.refresh_from_db()
        self.assertEqual(sub.recovery_flagged_at, first)
        self.assertEqual(sub.recovery_flag_source, 'graphite')

    # ── The alert to Lindani ────────────────────────────────────────────────

    def test_alert_goes_to_lindani_and_raises_a_task(self):
        from core.models import OmniTask

        sub = self._sub()
        flag_recovery_possible(sub)
        notify_recovery_flagged(sub)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn(LINDANI_EMAIL, msg.to)
        self.assertIn(sub.claim_reference, msg.subject)
        self.assertTrue(OmniTask.objects.filter(
            assignee=self.user, status=OmniTask.Status.PENDING).exists())

    def test_alert_to_lindani_is_not_copied_to_the_shared_exco_mailbox(self):
        """The alert carries a one-tap link into HER case. excoboard@ is a
        shared mailbox — anyone sitting in it could act as her."""
        sub = self._sub()
        flag_recovery_possible(sub)
        notify_recovery_flagged(sub)

        everyone = set(mail.outbox[0].to) | set(mail.outbox[0].cc or []) \
            | set(mail.outbox[0].bcc or [])
        self.assertNotIn(CFO_SHARED_MAILBOX, everyone)

    # ── The demand letter ───────────────────────────────────────────────────

    def test_demand_letter_pdf_is_built_from_the_case(self):
        sub = self._sub(assessor_fees=Decimal('2000.00'),
                        repair_costs=Decimal('50000.00'),
                        salvage_amount=Decimal('5000.00'))
        pdf = build_demand_letter_pdf(sub)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1500)

    def test_demand_letter_is_customer_facing_and_carries_no_internal_banner(self):
        """A third party must never see the red internal 'do not reply, log it
        in Omni' block — that banner is for staff mail only."""
        sub = self._sub()
        flag_recovery_possible(sub)
        notify_recovery_flagged(sub)
        alert_msg = mail.outbox[0]          # internal — keeps the banner
        mail.outbox = []

        send_demand_letter(sub, to_email='thirdparty@example.com',
                           pdf_bytes=build_demand_letter_pdf(sub))

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['thirdparty@example.com'])
        html = ''.join(body for body, mime in msg.alternatives
                       if mime == 'text/html')
        self.assertNotIn('Please do not reply to this email', html)

        # And prove that assertion is not vacuous: the SAME helper does put the
        # red banner on internal mail. Without this pair, a test that only
        # checks the banner is absent would pass even if the banner had been
        # switched off everywhere.
        internal_html = ''.join(
            b for b, m in alert_msg.alternatives if m == 'text/html')
        self.assertIn('Please do not reply to this email', internal_html)
        self.assertNotIn(CFO_SHARED_MAILBOX,
                         set(msg.to) | set(msg.cc or []) | set(msg.bcc or []))
        self.assertTrue(msg.attachments)

    def test_sending_the_letter_stamps_the_case_and_clears_the_task(self):
        from core.models import OmniTask

        sub = self._sub()
        flag_recovery_possible(sub)
        notify_recovery_flagged(sub)
        send_demand_letter(sub, to_email='thirdparty@example.com',
                           pdf_bytes=b'%PDF-1.4 stub')
        sub.refresh_from_db()

        self.assertIsNotNone(sub.demand_letter_sent_at)
        self.assertEqual(sub.demand_letter_sent_to, 'thirdparty@example.com')
        self.assertFalse(OmniTask.objects.filter(
            assignee=self.user,
            status__in=[OmniTask.Status.PENDING,
                        OmniTask.Status.IN_PROGRESS]).exists())

    # ── The 48-hour escalation ──────────────────────────────────────────────

    def _flagged_hours_ago(self, hours):
        sub = self._sub()
        flag_recovery_possible(
            sub, when=timezone.now() - timedelta(hours=hours))
        return sub

    def test_nothing_escalates_before_the_deadline(self):
        self._flagged_hours_ago(ESCALATION_HOURS - 1)
        self.assertEqual(
            due_for_escalation(Subrogation.objects.all()).count(), 0)

    def test_escalates_after_the_deadline_to_the_finance_manager(self):
        sub = self._flagged_hours_ago(ESCALATION_HOURS + 1)
        due = due_for_escalation(Subrogation.objects.all())
        self.assertEqual(list(due), [sub])

        mail.outbox = []
        escalate(sub)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(FINANCE_MANAGER_EMAIL, mail.outbox[0].to)
        self.assertIn(sub.claim_reference, mail.outbox[0].subject)

    def test_escalation_fires_exactly_once(self):
        sub = self._flagged_hours_ago(ESCALATION_HOURS + 5)
        mail.outbox = []
        call_command('subrogation_escalation_check')
        call_command('subrogation_escalation_check')
        call_command('subrogation_escalation_check')
        self.assertEqual(len(mail.outbox), 1)
        sub.refresh_from_db()
        self.assertIsNotNone(sub.escalated_at)

    def test_a_sent_demand_letter_stops_the_escalation(self):
        sub = self._flagged_hours_ago(ESCALATION_HOURS + 5)
        sub.demand_letter_sent_at = timezone.now()
        sub.save(update_fields=['demand_letter_sent_at'])
        mail.outbox = []
        call_command('subrogation_escalation_check')
        self.assertEqual(len(mail.outbox), 0)

    def test_escalation_survives_the_record_being_touched_repeatedly(self):
        """THE GUARD. Measure the 48 hours from `updated_at` — or from anything
        an editor can move — and the escalation is defeated by the most ordinary
        act there is: opening the case and saving it. Here the case is edited
        five times, as it would be while somebody 'looks into it', and it must
        still escalate, because the clock is anchored to when the flag ARRIVED.
        """
        sub = self._flagged_hours_ago(ESCALATION_HOURS + 3)
        flagged = sub.recovery_flagged_at

        for i in range(5):
            sub.notes = f'looking into it — note {i}'
            sub.save()
            sub.refresh_from_db()

        # updated_at has moved to now; recovery_flagged_at has not.
        self.assertGreater(sub.updated_at, flagged)
        self.assertEqual(sub.recovery_flagged_at, flagged)

        mail.outbox = []
        call_command('subrogation_escalation_check')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(FINANCE_MANAGER_EMAIL, mail.outbox[0].to)

    def test_escalated_case_still_escalates_only_once_after_more_editing(self):
        """And re-touching it AFTER the escalation must not make it fire again."""
        sub = self._flagged_hours_ago(ESCALATION_HOURS + 3)
        call_command('subrogation_escalation_check')
        mail.outbox = []
        sub.refresh_from_db()
        sub.notes = 'chased the insurer'
        sub.save()
        call_command('subrogation_escalation_check')
        self.assertEqual(len(mail.outbox), 0)

    # ── The Graphite flag reader ────────────────────────────────────────────

    def test_flag_ingest_is_quiet_when_graphite_has_sent_nothing(self):
        """Until TheRiskCo adds the field and the export, there is no snapshot.
        That is not an error and must not raise."""
        call_command('ingest_subrogation_flags')
        self.assertEqual(len(mail.outbox), 0)

    def test_flag_ingest_opens_the_case_and_alerts_once_across_two_nights(self):
        from integrations.models import GraphiteSnapshot

        User.objects.create(username='b9-su', is_superuser=True, is_active=True)
        payload = {'rows': [{'claim_reference': 'CLM-2026-777001',
                             'recovery_possible': True,
                             'claim_paid_amount': '42000.00',
                             'expected_recovery': '30000.00',
                             'third_party_insurer': 'Test Insurer Ltd'}]}
        GraphiteSnapshot.objects.create(dataset='subrogation_flags',
                                        payload=payload, row_count=1)
        call_command('ingest_subrogation_flags')
        call_command('ingest_subrogation_flags')          # the next night

        sub = Subrogation.objects.get(claim_reference='CLM-2026-777001')
        self.assertIsNotNone(sub.recovery_flagged_at)
        self.assertEqual(sub.recovery_flag_source, 'graphite')
        self.assertEqual(len(mail.outbox), 1)            # alerted once, not twice

    def test_flag_ingest_skips_rows_that_are_not_flagged(self):
        from integrations.models import GraphiteSnapshot

        User.objects.create(username='b9-su2', is_superuser=True, is_active=True)
        GraphiteSnapshot.objects.create(
            dataset='subrogation_flags', row_count=1,
            payload={'rows': [{'claim_reference': 'CLM-2026-777002',
                               'recovery_possible': False}]})
        call_command('ingest_subrogation_flags')
        self.assertFalse(
            Subrogation.objects.filter(claim_reference='CLM-2026-777002').exists())
        self.assertEqual(len(mail.outbox), 0)
