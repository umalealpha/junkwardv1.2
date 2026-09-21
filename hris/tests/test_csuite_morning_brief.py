"""CFO instruction, 11 Aug 2026: the CEO and CFO get their own morning brief.

They do not run Time Doctor, so `uid_for_employee_id` never matched them and the
sender skipped them outright — they received nothing at all. They must now get
the brief, WITHOUT the red "we saw no tracking from you, raise an IT ticket"
panel, which is a false alarm for a role that was never tracked.

Everyone else with no tracker match must still be skipped: for a tracked role a
missing match means broken data, and inventing a zero is the false warning the
guard exists to prevent.
"""
from __future__ import annotations

import datetime as _dt

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Company, UserProfile
from hris import morning_brief
from hris.models import HRISProfile
from hris.workforce_roles import NO_TRACKER_TITLES, no_tracker_title
from payroll.models import Employee

User = get_user_model()


class NoTrackerRoleTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='CSB', name='Brief Co.')

    def _profile(self, username, title):
        u = User.objects.create_user(username, f'{username}@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(user=u, defaults={'title': title})
        e = Employee.objects.create(
            employee_number=f'E{username}', full_name=f'Person {username}',
            job_title='Officer', department='Exec', company=self.company,
            email=f'{username}@alphadirect.co.bw', user=u)
        return HRISProfile.objects.create(employee=e)

    def test_ceo_and_cfo_are_no_tracker_roles(self):
        self.assertEqual(
            no_tracker_title(self._profile('theceo', UserProfile.Title.CEO)), 'ceo')
        self.assertEqual(
            no_tracker_title(self._profile('thecfo', UserProfile.Title.CFO)), 'cfo')
        self.assertEqual(
            no_tracker_title(self._profile('thecoo', UserProfile.Title.COO)), 'coo')
        self.assertIn('ceo', NO_TRACKER_TITLES)
        self.assertIn('cfo', NO_TRACKER_TITLES)
        self.assertIn('coo', NO_TRACKER_TITLES)

    def test_an_ordinary_role_is_not_exempt(self):
        """An accountant with no tracker is BROKEN DATA, not 'not applicable'."""
        p = self._profile('anacct', UserProfile.Title.ACCOUNTANT)
        self.assertNotIn(no_tracker_title(p), NO_TRACKER_TITLES)

    def test_an_unreadable_profile_defaults_to_tracked(self):
        """Safe default: keep the old behaviour rather than silently exempt."""
        self.assertEqual(no_tracker_title(None), '')
        self.assertNotIn(no_tracker_title(None), NO_TRACKER_TITLES)


class TrackingNaRenderTests(TestCase):
    """The brief renders WITHOUT the broken-tracker alarm."""

    def _html(self, **over):
        ctx = dict(
            name='Chief', day=_dt.date(2026, 8, 11), required=4.5,
            tracked_yesterday=0, week_hours=0, rank=None, team_size=10,
            winner_name='', tasks=[], announcements=[],
            leave={'taken_month': 0, 'pending_days': 0, 'pending_count': 0,
                   'annual_available': None, 'annual_entitlement': None},
            approvals={'count': 0, 'streams': []})
        ctx.update(over)
        return morning_brief.build_morning_html(**ctx)

    def test_no_tracker_role_is_not_told_to_raise_an_it_ticket(self):
        html = self._html(tracking_na=True)
        self.assertNotIn('Create an IT ticket', html)
        self.assertNotIn("didn't see any Time Doctor tracking", html)
        self.assertIn('no time tracking on your role', html.lower())

    def test_a_tracked_person_on_zero_still_gets_the_warning(self):
        """The guard must survive — this is the case it was built for."""
        html = self._html(tracking_ok=False)
        self.assertIn('Create an IT ticket', html)

    def test_the_brief_still_carries_the_rest_of_the_content(self):
        html = self._html(tracking_na=True, tasks=[{'title': 'Sign the PO',
                                                    'priority': 'high'}])
        self.assertIn('Sign the PO', html)


class SendMorningBriefCommandTests(TestCase):
    """The REAL command, not the helper — proves delivery, not intent.

    The helper tests above prove the classification and the rendering. Neither
    proves the sender actually sends, and the whole bug was that it silently did
    not. Mirrors the mocked-Time-Doctor harness in test_consolidated_brief.
    """

    DAY = _dt.date(2026, 7, 29)          # ordinary Wednesday, not a BW holiday

    class _FakeTD:
        configured = True
        company_id = 'c-test'

        def __init__(self, users):
            self._users = users

        def users(self):
            return list(self._users)

        def worklog(self, day_from, day_to, user_ids=None):
            return [[{'userId': u['id'], 'time': 6 * 3600,
                      'start': '2026-07-29T06:00:00', 'mode': 'computer'}
                     for u in self._users]]

        def timeuse(self, day_from, day_to, user_ids=None):
            return []

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='CMD', name='Command Co.')

    def _person(self, username, title):
        u = User.objects.create_user(username, f'{username}@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(user=u, defaults={'title': title})
        e = Employee.objects.create(
            employee_number=f'CMD-{username}', full_name=f'Person {username}',
            email=f'{username}@alphadirect.co.bw', status='active',
            department='Exec', company=self.company, user=u)
        HRISProfile.objects.create(employee=e)
        return e

    def _run(self, paid_ids, td_users):
        from io import StringIO
        from unittest import mock
        from django.core.management import call_command
        # At least one REAL tracked Time Doctor user, as on prod (98 of them).
        # An EMPTY Time Doctor list is not the situation being tested and breaks
        # the aggregation upstream — the case that matters is a populated Time
        # Doctor that simply has no row for the CEO.
        fake = self._FakeTD(td_users)
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                        return_value=fake), \
             mock.patch('hris.morning_brief.aria_note', return_value=''), \
             mock.patch('hris.eligibility._paid_employee_ids', return_value=paid_ids):
            call_command('send_morning_brief', '--date', self.DAY.isoformat(),
                         '--force', stdout=StringIO(), stderr=StringIO())

    def _brief_for(self, addr):
        from django.core import mail
        for m in mail.outbox:
            if addr in m.to and m.subject.startswith('🌅'):
                return m
        return None

    def test_an_unmatched_ceo_is_sent_a_brief_and_an_unmatched_accountant_is_not(self):
        from django.test import override_settings
        ceo = self._person('cmdceo', UserProfile.Title.CEO)
        acct = self._person('cmdacct', UserProfile.Title.ACCOUNTANT)
        tracked = self._person('cmdtrk', UserProfile.Title.OPERATIONS)
        # MIRROR PROD. On prod the CEO and CFO carry
        # TrackingDirective(expected_to_track=False) — they genuinely are not
        # expected to track — and eligibility.tracking_profiles() drops them
        # BEFORE the send loop. Without this row the test passes while prod
        # silently delivers nothing, which is exactly what Fable caught.
        from hris.models import TrackingDirective
        TrackingDirective.objects.create(employee=ceo, expected_to_track=False)
        td_users = [{'id': 'u-trk', 'name': 'Person cmdtrk',
                     'email': 'cmdtrk@alphadirect.co.bw'}]
        with override_settings(
                EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                WORKFORCE_DATA_GUARD_ENABLED=False, ELRA_PERF_ENABLED=False,
                CONSOLIDATED_EMAILS_ENABLED=False):
            self._run({ceo.id, acct.id, tracked.id}, td_users)
            got_ceo = self._brief_for('cmdceo@alphadirect.co.bw')
            got_acct = self._brief_for('cmdacct@alphadirect.co.bw')

        self.assertIsNotNone(got_ceo, 'the CEO must receive a brief with no tracker')
        html = got_ceo.alternatives[0][0] if got_ceo.alternatives else got_ceo.body
        # ...and must NOT be told their tracker is broken
        self.assertNotIn('Create an IT ticket', html)
        # the tracked role with no match is still skipped — the guard survives
        self.assertIsNone(got_acct,
                          'an unmatched TRACKED role must still be skipped')
        # and the genuinely tracked person is unaffected
        self.assertIsNotNone(self._brief_for('cmdtrk@alphadirect.co.bw'))
