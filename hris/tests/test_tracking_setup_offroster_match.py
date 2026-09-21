"""Who-tracks must OFFER a Time Doctor link to an active employee who has no
HRISProfile (CFO 2026-09-15).

tracking_roster() is built from HRISProfile rows, so an active employee without
one was never handed to the matcher and never appeared on the screen. Their
Time Doctor account stayed unlinked for ever and Omni told them "not linked to
any Time Doctor account" — seven real people were sitting like that, with hours
Time Doctor had genuinely recorded.

The link still only COUNTS once a human ticks it: hours resolve through the
CONFIRMED map, and these rows come back confirmed=False.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Company, Currency
from payroll.models import Employee

User = get_user_model()


class _FakeTDClient:
    configured = True

    def __init__(self, users):
        self._users = users

    @classmethod
    def from_settings(cls):
        raise NotImplementedError        # patched per-test

    def users(self):
        return self._users


class TrackingSetupOffRosterMatchTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        # Active employee with NO HRISProfile — invisible to tracking_roster().
        cls.emp = Employee.objects.create(
            employee_number='E-OFFROSTER', full_name='Kgosi Seboko',
            company=cls.co, status='active')
        cls.admin = User.objects.create_superuser(
            username='td-setup-admin', email='td-setup-admin@alphadirect.co.bw',
            password='x')

    def _get(self, td_users):
        client = _FakeTDClient(td_users)
        with patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                   return_value=client):
            self.client.force_login(self.admin)
            return self.client.get(reverse('v1-workforce-tracking-setup'))

    def test_off_roster_employee_is_offered_the_matching_td_account(self):
        """The whole point: she is not on the roster, so before the fix this
        row did not exist at all and HR had nothing to tick."""
        resp = self._get([{'id': 'aqeab18gdJpvI5lT', 'name': 'Kgosi Seboko',
                           # the Windows-SID address that defeats email matching
                           'email': 'S-1-5-21-2646779368-1787180032@corp.local'}])
        self.assertEqual(resp.status_code, 200)
        rows = [r for r in resp.json()['items']
                if str(r['employee_id']) == str(self.emp.id)]
        self.assertEqual(len(rows), 1,
                         'active employee with no HRISProfile was not offered on '
                         'the who-tracks screen')
        row = rows[0]
        self.assertEqual(row['matched_td'], 'Kgosi Seboko')
        self.assertEqual(row['td_user_id'], 'aqeab18gdJpvI5lT')
        # A proposal, never a decision: unticked, and not reported on.
        self.assertFalse(row['confirmed'])
        self.assertFalse(row['expected'])
        self.assertEqual(row['source'], 'no-profile')

    def test_off_roster_employee_without_a_td_account_is_not_listed(self):
        """Only people with something to tick appear — the screen must not grow
        into a second staff directory."""
        resp = self._get([{'id': 'zzzz', 'name': 'Someone Else',
                           'email': 'someone@alphadirect.co.bw'}])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [r for r in resp.json()['items']
             if str(r['employee_id']) == str(self.emp.id)], [])
