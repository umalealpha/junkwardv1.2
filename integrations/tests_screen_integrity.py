"""
integrations/tests_screen_integrity.py

Tests for the Screen-Integrity monitor (CFO 2026-09-06): persistence of the
frozen-screen sweep and the gated pull/re-scan API.

Run:  python manage.py test integrations.tests_screen_integrity
"""
from __future__ import annotations

import datetime
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from integrations.models import ScreenIntegrityScan, ScreenIntegrityFlag
from integrations.screen_integrity_store import persist_day
from integrations.td_screenshot_integrity import ScreenSignal


def _sig(name, suspicion, *, frozen=0.6, hours=3.7, mouse=0.9, ident=0.63, shots=100):
    s = ScreenSignal(user_id=f'uid-{name}', name=name)
    s.suspicion = suspicion
    s.frozen_typing_pct = frozen
    s.frozen_typing_hours = hours
    s.mouse_dead_pct = mouse
    s.identical_pct = ident
    s.shots = shots
    s.reasons = ['heavy typing on a frozen screen'] if suspicion != 'clean' else []
    return s


class PersistDayTests(TestCase):
    def test_persists_summary_and_only_flagged_rows(self):
        day = datetime.date(2026, 9, 5)
        sigs = [
            _sig('Snehal', 'suspicious'),
            _sig('Lame', 'watch', frozen=0.49, hours=1.6),
            _sig('Clean Carol', 'clean', frozen=0.0, hours=0.0),
        ]
        scan = persist_day(day, sigs)

        self.assertEqual(scan.people_checked, 3)   # everyone counted…
        self.assertEqual(scan.suspicious, 1)
        self.assertEqual(scan.watch, 1)
        # …but only the two flagged people get a row (clean is never stored).
        self.assertEqual(scan.flags.count(), 2)
        self.assertFalse(ScreenIntegrityFlag.objects.filter(name='Clean Carol').exists())
        susp = scan.flags.get(name='Snehal')
        self.assertEqual(susp.suspicion, 'suspicious')
        self.assertEqual(float(susp.frozen_typing_pct), 60.0)

    def test_rescan_is_idempotent(self):
        day = datetime.date(2026, 9, 5)
        persist_day(day, [_sig('Snehal', 'suspicious'), _sig('Lame', 'watch')])
        # Re-scan the same day: Lame now clean, Snehal still suspicious.
        scan = persist_day(day, [_sig('Snehal', 'suspicious'), _sig('Lame', 'clean')])

        self.assertEqual(ScreenIntegrityScan.objects.filter(day=day).count(), 1)  # no dup day
        self.assertEqual(scan.watch, 0)
        self.assertEqual(scan.flags.count(), 1)          # stale Lame row gone
        self.assertFalse(scan.flags.filter(name='Lame').exists())

    def test_no_data_day_still_recorded(self):
        day = datetime.date(2026, 9, 4)
        scan = persist_day(day, [], status=ScreenIntegrityScan.Status.NO_DATA)
        self.assertEqual(scan.status, 'no_data')
        self.assertEqual(scan.flags.count(), 0)


class ApiGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ess = User.objects.create_user('ess', email='ess@ad.co.bw', password='x')
        cls.admin = User.objects.create_superuser('root', email='root@ad.co.bw', password='x')
        persist_day(datetime.date(2026, 9, 5), [_sig('Snehal', 'suspicious')])

    def _c(self, user):
        c = APIClient(); c.force_authenticate(user=user); return c

    def test_ordinary_staff_blocked(self):
        r = self._c(self.ess).get('/api/v1/hris/screen-integrity/?days=7')
        self.assertEqual(r.status_code, 403)

    def test_admin_sees_stored_sweeps(self):
        r = self._c(self.admin).get('/api/v1/hris/screen-integrity/?days=7')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['totals']['suspicious'], 1)
        self.assertEqual(r.data['scans'][0]['flags'][0]['name'], 'Snehal')

    def test_rescan_rejects_bad_date(self):
        r = self._c(self.admin).post('/api/v1/hris/screen-integrity/rescan/',
                                     {'date': 'not-a-date'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_rescan_rejects_future_date(self):
        future = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        r = self._c(self.admin).post('/api/v1/hris/screen-integrity/rescan/',
                                     {'date': future}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_rescan_when_timedoctor_unconfigured(self):
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings') as m:
            m.return_value = mock.Mock(configured=False)
            r = self._c(self.admin).post('/api/v1/hris/screen-integrity/rescan/',
                                         {'date': '2026-09-05'}, format='json')
        self.assertEqual(r.status_code, 503)
