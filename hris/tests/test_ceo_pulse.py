from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase

from hris import ceo_pulse
from hris.ceo_pulse import PREVIEW_TO, PULSE_CC, PULSE_TO


class CeoPulseTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.sample = {
            'td': {
                'available': True,
                'reason': '',
                # Keys exactly as exceptions_report._summary and
                # workforce_pulse.team_leaderboard produce them.
                'summary': {
                    'tracked': 100,
                    'did_not_track': 2,
                    'roster': 120,
                    'total_h': 3120.5,
                    'avg_h': 31.2,
                    'prod_h': 95,
                },
                'leaderboard': [
                    {'dept': 'Executive', 'heads': 4, 'avg_prod_h': 7.2, 'avg_h': 8.1, 'total_h': 32.4},
                ],
                'shortfall': [],
                'late': [],
            },
            'excuses': {
                'total': 5,
                'by_reason': [{'reason': 'On leave', 'count': 3}],
                'unresolved': 2,
                'repeated': 1,
            },
            'managers': [{'name': 'Unami Butale', 'count': 3}],
            'tasks': {
                'overdue': 4,
                'done': 7,
                'top_assignees': [{'name': 'Alice Smith', 'overdue': 2}],
            },
            'flight_risk': {
                'counts': {'high': 2, 'med': 3, 'low': 10},
                'top': [{'name': 'Bob Stone', 'band': 'high', 'score': 90}],
                'error': None,
            },
            'salary_at_risk': {
                'bwp': Decimal('12344.5'),
                'people': 2,
                'basis': 'latest monthly gross of high-risk staff',
            },
            'actions': 12,
            'complete': True,
            'missing': [],
        }

    def test_week_window(self):
        # Sunday 14 Jan 2024 -> week ending Saturday 13 Jan
        self.assertEqual(
            ceo_pulse.week_window(date(2024, 1, 14)),
            (date(2024, 1, 8), date(2024, 1, 13)),
        )
        # Wednesday 10 Jan 2024 -> most recent completed week ending Sat 6 Jan
        self.assertEqual(
            ceo_pulse.week_window(date(2024, 1, 10)),
            (date(2024, 1, 1), date(2024, 1, 6)),
        )

    def test_preview_goes_only_to_preview_recipient(self):
        with mock.patch('hris.ceo_pulse.collect', return_value=self.sample), \
             mock.patch('hris.ceo_pulse.ceo_question', return_value=None), \
             mock.patch('core.notifications.no_reply_banner', return_value=''):
            result = ceo_pulse.send_pulse(
                preview=True,
                dry_run=False,
                today=date(2024, 1, 14),
                td_client=None,
            )

        self.assertTrue(result['sent'])
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, list(PREVIEW_TO))
        self.assertEqual(msg.cc, [])

    def test_send_with_incomplete_td_is_held_and_outbox_empty(self):
        incomplete = {
            **self.sample,
            'complete': False,
            'missing': ['Time Doctor data for the week'],
            'td': {
                'available': False,
                'reason': 'Time Doctor client is not configured.',
                'summary': {},
                'leaderboard': [],
                'shortfall': [],
                'late': [],
            },
        }

        with mock.patch('hris.ceo_pulse.collect', return_value=incomplete), \
             mock.patch('hris.ceo_pulse.ceo_question', return_value=None), \
             mock.patch('core.notifications.no_reply_banner', return_value=''):
            result = ceo_pulse.send_pulse(
                preview=False,
                dry_run=False,
                today=date(2024, 1, 14),
                td_client=None,
            )

        self.assertTrue(result['held'])
        self.assertFalse(result['sent'])
        self.assertEqual(len(mail.outbox), 0)

    def test_complete_send_uses_exact_recipient_constants(self):
        with mock.patch('hris.ceo_pulse.collect', return_value=self.sample), \
             mock.patch('hris.ceo_pulse.ceo_question', return_value=None), \
             mock.patch('core.notifications.no_reply_banner', return_value=''):
            result = ceo_pulse.send_pulse(
                preview=False,
                dry_run=False,
                today=date(2024, 1, 14),
                td_client=object(),
            )

        self.assertTrue(result['sent'])
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, list(PULSE_TO))
        self.assertEqual(msg.cc, list(PULSE_CC))

    def test_salary_appears_once_as_aggregate(self):
        with mock.patch('hris.ceo_pulse.collect', return_value=self.sample), \
             mock.patch('hris.ceo_pulse.ceo_question', return_value=None), \
             mock.patch('core.notifications.no_reply_banner', return_value=''):
            ceo_pulse.send_pulse(
                preview=True,
                dry_run=False,
                today=date(2024, 1, 14),
                td_client=None,
            )

        msg = mail.outbox[0]
        html = msg.alternatives[0][0]
        self.assertEqual(html.count('BWP '), 1)
        self.assertIn('BWP 12,345', html)

    def test_html_escapes_malicious_name(self):
        data = self.sample
        data = {
            **data,
            'flight_risk': {
                **data['flight_risk'],
                'top': [
                    {
                        'name': '<script>alert(1)</script>',
                        'band': 'high',
                        'score': 90,
                    },
                ],
            },
        }

        html = ceo_pulse.build_html(
            data,
            monday=date(2024, 1, 8),
            saturday=date(2024, 1, 13),
            question=None,
            preview=True,
        )

        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)

    def test_incomplete_strip_present_when_not_complete(self):
        incomplete = {
            **self.sample,
            'complete': False,
            'missing': ['Time Doctor data for the week'],
            'td': {
                'available': False,
                'reason': 'Time Doctor client is not configured.',
                'summary': {},
                'leaderboard': [],
                'shortfall': [],
                'late': [],
            },
        }

        html = ceo_pulse.build_html(
            incomplete,
            monday=date(2024, 1, 8),
            saturday=date(2024, 1, 13),
            question=None,
            preview=False,
        )

        self.assertIn('INCOMPLETE — this will not go to the CEO', html)
        self.assertIn('Missing: Time Doctor data for the week', html)

    def test_ai_failure_still_builds_html_without_question_block(self):
        with mock.patch('core.ai_assist.reasoning_complete',
                        side_effect=RuntimeError('AI unavailable')):
            question = ceo_pulse.ceo_question(self.sample)

        self.assertIsNone(question)

        html = ceo_pulse.build_html(
            self.sample,
            monday=date(2024, 1, 8),
            saturday=date(2024, 1, 13),
            question=None,
            preview=True,
        )
        self.assertNotIn('One question for Monday', html)

    def test_command_send_with_stale_td_is_held(self):
        incomplete = {
            **self.sample,
            'complete': False,
            'missing': ['Time Doctor data for the week'],
            'td': {
                'available': False,
                'reason': 'Time Doctor client is not configured.',
                'summary': {},
                'leaderboard': [],
                'shortfall': [],
                'late': [],
            },
        }

        mock_from_settings = mock.MagicMock()
        mock_from_settings.configured = False

        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                        return_value=mock_from_settings), \
             mock.patch('hris.ceo_pulse.collect', return_value=incomplete), \
             mock.patch('hris.ceo_pulse.ceo_question', return_value=None), \
             mock.patch('core.notifications.no_reply_banner', return_value=''):
            call_command('send_ceo_pulse', send=True, today='2024-01-14')

        self.assertEqual(len(mail.outbox), 0)


class CeoPulseJudgeFindingsTests(TestCase):
    def test_partial_time_doctor_pull_holds_the_send(self):
        from hris import ceo_pulse
        partial = {'summary': {'tracked': 3, 'roster': 120, 'prod_h': 5, 'total_h': 10}}
        with mock.patch('hris.exceptions_report.compute_weekly', return_value=partial):
            td = ceo_pulse._collect_td(date(2026, 9, 14), date(2026, 9, 19), object())
        self.assertFalse(td['available'])
        self.assertIn('covers only', td['reason'])

    def test_league_and_late_use_the_real_report(self):
        from hris import ceo_pulse
        t = CeoPulseTests(); t.setUp()
        html = ceo_pulse.build_html(t.sample, monday=date(2026, 9, 14),
                                    saturday=date(2026, 9, 19), question=None, preview=False)
        self.assertIn('Executive', html)
        self.assertIn('7.2', html)
        self.assertIn('n/a', html)

    def test_fewer_than_three_people_hides_the_salary(self):
        from types import SimpleNamespace
        from payroll.models import Employee
        a = Employee.objects.create(employee_number='PUL-1', full_name='Same Name', status=Employee.Status.ACTIVE)
        b = Employee.objects.create(employee_number='PUL-2', full_name='Same Name', status=Employee.Status.ACTIVE)
        slips = [SimpleNamespace(employee_id=a.pk, gross_amount=Decimal('10000')),
                 SimpleNamespace(employee_id=b.pk, gross_amount=Decimal('20000'))]
        with mock.patch('hris.ceo_pulse.Payslip') as P:
            P.objects.filter.return_value.exclude.return_value.order_by.return_value = slips
            two = ceo_pulse._salary_at_risk([str(a.pk), str(b.pk)], None)
            # Identity, not name: asking for 'a' alone must not pull in 'b' (same name).
            P.objects.filter.return_value.exclude.return_value.order_by.return_value = slips[:1]
            one = ceo_pulse._salary_at_risk([str(a.pk)], None)
        self.assertIsNone(two['bwp'])
        self.assertIn('fewer than 3', two['basis'])
        self.assertEqual(one['people'], 1)
        self.assertIsNone(one['bwp'])
