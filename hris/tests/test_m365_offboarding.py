from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Company, Currency
from hris.m365_offboarding import run
from hris.models import OffboardingCase
from payroll.models import Employee

TODAY = date(2026, 9, 21)


def _resp(code):
    r = MagicMock()
    r.status_code = code
    return r


class LeaverM365SwitchOffTests(TestCase):
    def setUp(self):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        self.company = Company.objects.create(code='TST1', name='Test Co One')
        token = patch('licensing.services.graph._token', return_value='tok')
        token.start()
        self.addCleanup(token.stop)

    def _case(self, name, email, last_day, **employee_fields):
        employee = Employee.objects.create(
            full_name=name, employee_number=name[:6].upper(), email=email,
            company=self.company, status='active', **employee_fields)
        return OffboardingCase.objects.create(employee=employee, last_working_day=last_day)

    @override_settings(M365_LEAVER_DISABLE='enforce')
    @patch('hris.m365_offboarding.requests')
    def test_switches_off_only_after_the_last_working_day(self, req):
        req.patch.return_value = _resp(204)
        req.post.return_value = _resp(204)
        gone = self._case('Past Leaver', 'past@alphadirect.co.bw', date(2026, 9, 18))
        today_leaver = self._case('Today Leaver', 'today@alphadirect.co.bw', TODAY)

        rows = run(commit=True, today=TODAY)

        self.assertEqual([(r[0].pk, r[1]) for r in rows], [(gone.pk, 'switched off')])
        req.patch.assert_called_once()
        self.assertIn('past@alphadirect.co.bw', req.patch.call_args.args[0])
        self.assertEqual(req.patch.call_args.kwargs['json'], {'accountEnabled': False})
        gone.refresh_from_db(); today_leaver.refresh_from_db()
        self.assertIsNotNone(gone.m365_disabled_at)
        self.assertIsNone(today_leaver.m365_disabled_at)
        # Second run does nothing: already switched off.
        self.assertEqual(run(commit=True, today=TODAY), [])

    @override_settings(M365_LEAVER_DISABLE='enforce')
    @patch('hris.m365_offboarding.requests')
    def test_missing_permission_is_recorded_not_marked_done(self, req):
        req.patch.return_value = _resp(403)
        case = self._case('No Perm', 'noperm@alphadirect.co.bw', date(2026, 9, 1))

        rows = run(commit=True, today=TODAY)

        self.assertEqual(rows[0][1], 'failed')
        case.refresh_from_db()
        self.assertIsNone(case.m365_disabled_at)
        self.assertIn('permission', case.m365_note)

    @override_settings(M365_LEAVER_DISABLE='enforce')
    @patch('hris.m365_offboarding.requests')
    def test_protected_people_are_never_switched_off(self, req):
        get_user_model().objects.create_superuser(username='boss', email='boss@alphadirect.co.bw', password='x')
        self._case('Cfo', 'pganesharajah@alphadirect.co.bw', date(2026, 9, 1))
        self._case('Super Admin', 'boss@alphadirect.co.bw', date(2026, 9, 1))
        self._case('Contractor', 'contractor@alphadirect.co.bw', date(2026, 9, 1), keep_access_after_exit=True)
        self._case('Outside', 'someone@gmail.com', date(2026, 9, 1))
        cancelled = self._case('Cancelled', 'cancelled@alphadirect.co.bw', date(2026, 9, 1))
        cancelled.status = OffboardingCase.Status.CANCELLED
        cancelled.save()

        rows = run(commit=True, today=TODAY)

        self.assertEqual({r[1] for r in rows}, {'skipped'})
        self.assertEqual(len(rows), 4)
        req.patch.assert_not_called()

    @override_settings(M365_LEAVER_DISABLE='report')
    @patch('hris.m365_offboarding.requests')
    def test_report_mode_never_calls_microsoft(self, req):
        self._case('Report Only', 'report@alphadirect.co.bw', date(2026, 9, 1))
        rows = run(commit=True, today=TODAY)
        self.assertEqual(rows[0][1], 'would switch off')
        req.patch.assert_not_called()
        # Next morning: not reported again.
        self.assertEqual(run(commit=True, today=TODAY)[0][1], 'already reported')

    @override_settings(M365_LEAVER_DISABLE='enforce')
    @patch('hris.m365_offboarding.requests')
    def test_a_network_error_does_not_stop_the_other_leavers(self, req):
        import requests as real_requests
        req.RequestException = real_requests.RequestException
        req.patch.side_effect = [real_requests.ConnectionError('down'), _resp(204)]
        req.post.return_value = _resp(204)
        first = self._case('First Leaver', 'first@alphadirect.co.bw', date(2026, 9, 1))
        second = self._case('Second Leaver', 'second@alphadirect.co.bw', date(2026, 9, 2))

        rows = run(commit=True, today=TODAY)

        self.assertEqual([r[1] for r in rows], ['failed', 'switched off'])
        first.refresh_from_db(); second.refresh_from_db()
        self.assertIsNone(first.m365_disabled_at)
        self.assertIsNotNone(second.m365_disabled_at)

    @override_settings(M365_LEAVER_DISABLE='off')
    @patch('hris.m365_offboarding.requests')
    def test_command_off_does_nothing(self, req):
        self._case('Off Mode', 'off@alphadirect.co.bw', date(2026, 9, 1))
        call_command('disable_leaver_m365', '--commit')
        req.patch.assert_not_called()
