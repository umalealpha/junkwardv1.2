"""
integrations/tests_screen_integrity.py

Tests for the Screen-Integrity monitor (CFO 2026-09-06): persistence of the
frozen-screen sweep and the gated pull/re-scan API.

Run:  python manage.py test integrations.tests_screen_integrity
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from integrations.models import ScreenIntegrityScan, ScreenIntegrityFlag
from integrations.screen_integrity_store import persist_day
from integrations.td_screenshot_integrity import ScreenSignal


def _sig(name, suspicion, *, frozen=0.6, hours=3.7, mouse=0.9, ident=0.63, shots=100,
         idle_pct=0.0, idle_hours=0.0):
    s = ScreenSignal(user_id=f'uid-{name}', name=name)
    s.suspicion = suspicion
    s.frozen_typing_pct = frozen
    s.frozen_typing_hours = hours
    s.mouse_dead_pct = mouse
    s.identical_pct = ident
    s.shots = shots
    s.idle_frozen_pct = idle_pct
    s.idle_frozen_hours = idle_hours
    s.reasons = ['heavy typing on a frozen screen'] if suspicion != 'clean' else []
    return s


class PersistDayTests(TestCase):
    def test_persists_summary_and_only_flagged_rows(self):
        day = datetime.date(2026, 9, 5)
        sigs = [
            _sig('Sus Pect', 'suspicious'),
            _sig('Watch Person', 'watch', frozen=0.49, hours=1.6),
            _sig('Clean Carol', 'clean', frozen=0.0, hours=0.0),
        ]
        scan = persist_day(day, sigs)

        self.assertEqual(scan.people_checked, 3)   # everyone counted…
        self.assertEqual(scan.suspicious, 1)
        self.assertEqual(scan.watch, 1)
        # …but only the two flagged people get a row (clean is never stored).
        self.assertEqual(scan.flags.count(), 2)
        self.assertFalse(ScreenIntegrityFlag.objects.filter(name='Clean Carol').exists())
        susp = scan.flags.get(name='Sus Pect')
        self.assertEqual(susp.suspicion, 'suspicious')
        self.assertEqual(float(susp.frozen_typing_pct), 60.0)

    def test_rescan_is_idempotent(self):
        day = datetime.date(2026, 9, 5)
        persist_day(day, [_sig('Sus Pect', 'suspicious'), _sig('Watch Person', 'watch')])
        # Re-scan the same day: the watch row now clean, the suspicious one still suspicious.
        scan = persist_day(day, [_sig('Sus Pect', 'suspicious'), _sig('Watch Person', 'clean')])

        self.assertEqual(ScreenIntegrityScan.objects.filter(day=day).count(), 1)  # no dup day
        self.assertEqual(scan.watch, 0)
        self.assertEqual(scan.flags.count(), 1)          # stale watch row gone
        self.assertFalse(scan.flags.filter(name='Watch Person').exists())

    def test_no_data_day_still_recorded(self):
        day = datetime.date(2026, 9, 4)
        scan = persist_day(day, [], status=ScreenIntegrityScan.Status.NO_DATA)
        self.assertEqual(scan.status, 'no_data')
        self.assertEqual(scan.flags.count(), 0)


class IdleFlagRoundTripTests(TestCase):
    """The idle-frozen rule's evidence must survive being saved and read back.
    A row raised by that rule has frozen_typing_* = 0 by definition, so if its own
    two columns are not persisted the HR screen shows a manager "suspicious — 0%,
    0.0h" and the weekly email totals 0.0 hours. Regression test for that."""

    def test_idle_evidence_is_stored_and_returned(self):
        from hris.screen_integrity_views import _flag_json
        day = datetime.date(2026, 9, 21)
        # The real measured case: 70.1% dead-and-frozen, 5.79 credited hours.
        persist_day(day, [_sig('Idle Case', 'suspicious', frozen=0.005, hours=0.04,
                               idle_pct=0.701, idle_hours=5.79, shots=187)])
        row = ScreenIntegrityFlag.objects.get(day=day, name='Idle Case')
        self.assertEqual(float(row.idle_frozen_pct), 70.1)
        self.assertEqual(float(row.idle_frozen_hours), 5.79)
        j = _flag_json(row)
        self.assertEqual(j['idle_frozen_pct'], 70.1)
        self.assertEqual(j['idle_frozen_hours'], 5.79)


class RaisedByTests(TestCase):
    """The screen must show only the numbers of the rule that raised the row, and
    the SERVER must be the one that decides which. The first version of this gated
    the two metric blocks in the .tsx file against hard-coded 25 / 60 — the day
    anyone tunes PHANTOM_DAY_PCT or IDLE_DAY_PCT in Python, those copies drift and
    the screen silently shows the wrong block."""

    def _row(self, **kw):
        from integrations.models import ScreenIntegrityFlag, ScreenIntegrityScan
        scan, _ = ScreenIntegrityScan.objects.get_or_create(
            day=datetime.date(2026, 9, 21),
            defaults={'people_checked': 100, 'status': 'ok'})
        return ScreenIntegrityFlag(scan=scan, day=scan.day, suspicion='suspicious', **kw)

    def test_idle_raised_row_hides_the_typing_numbers(self):
        from hris.screen_integrity_views import _flag_json
        # The real 21-Sep case: 0.5% typing is noise, 70.1% idle is the finding.
        r = self._row(td_user_id='u1', name='Idle Case',
                      frozen_typing_pct=Decimal('0.5'), frozen_typing_hours=Decimal('0.04'),
                      idle_frozen_pct=Decimal('70.1'), idle_frozen_hours=Decimal('5.79'))
        self.assertEqual(_flag_json(r)['raised_by'], 'idle')

    def test_typing_raised_row_hides_the_idle_numbers(self):
        from hris.screen_integrity_views import _flag_json
        r = self._row(td_user_id='u2', name='Typing Case',
                      frozen_typing_pct=Decimal('47.3'), frozen_typing_hours=Decimal('3.14'),
                      idle_frozen_pct=Decimal('6.2'), idle_frozen_hours=Decimal('0.41'))
        self.assertEqual(_flag_json(r)['raised_by'], 'typing')

    def test_a_row_tripping_both_rules_shows_both(self):
        from hris.screen_integrity_views import _flag_json
        r = self._row(td_user_id='u3', name='Both Case',
                      frozen_typing_pct=Decimal('33.0'), frozen_typing_hours=Decimal('2.25'),
                      idle_frozen_pct=Decimal('67.0'), idle_frozen_hours=Decimal('4.50'))
        self.assertEqual(_flag_json(r)['raised_by'], 'both')

    def test_a_pre_migration_row_still_reads_as_typing(self):
        from hris.screen_integrity_views import _flag_json
        # Rows written before the idle rule existed carry 0 in the new columns.
        r = self._row(td_user_id='u4', name='Old Row',
                      frozen_typing_pct=Decimal('60.0'), frozen_typing_hours=Decimal('3.7'))
        j = _flag_json(r)
        self.assertEqual(j['raised_by'], 'typing')
        self.assertEqual(j['idle_frozen_pct'], 0.0)

    def test_a_short_day_does_not_show_a_typing_figure_it_never_earned(self):
        """Fable 5.1, 21-Sep. On a SHORT tracked day the two rules' percentage legs
        and hours legs disagree: 3.6 hours at 25% frozen-typing is only 0.9h, under
        the typing rule's own floor, so that rule never fired. Checking percentages
        alone would print "frozen-typing 25%" in red beside a person the IDLE rule
        flagged — the exact accusation this function exists to prevent."""
        from hris.screen_integrity_views import _flag_json
        r = self._row(td_user_id='u5', name='Short Day',
                      frozen_typing_pct=Decimal('25.0'), frozen_typing_hours=Decimal('0.90'),
                      idle_frozen_pct=Decimal('60.0'), idle_frozen_hours=Decimal('2.16'))
        self.assertEqual(_flag_json(r)['raised_by'], 'idle')

    def test_the_thresholds_come_from_the_detector_not_a_copy(self):
        # If anyone re-hard-codes 25/60 anywhere, this is the test that should have
        # caught it. The view must read the detector's own constants.
        import hris.screen_integrity_views as v
        from integrations import td_screenshot_integrity as det
        self.assertIs(v.PHANTOM_DAY_PCT, det.PHANTOM_DAY_PCT)
        self.assertIs(v.IDLE_DAY_PCT, det.IDLE_DAY_PCT)


class ApiGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ess = User.objects.create_user('ess', email='ess@ad.co.bw', password='x')
        cls.admin = User.objects.create_superuser('root', email='root@ad.co.bw', password='x')
        persist_day(datetime.date(2026, 9, 5), [_sig('Sus Pect', 'suspicious')])

    def _c(self, user):
        c = APIClient(); c.force_authenticate(user=user); return c

    def test_ordinary_staff_blocked(self):
        r = self._c(self.ess).get('/api/v1/hris/screen-integrity/?days=7')
        self.assertEqual(r.status_code, 403)

    def test_admin_sees_stored_sweeps(self):
        r = self._c(self.admin).get('/api/v1/hris/screen-integrity/?days=7')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['totals']['suspicious'], 1)
        self.assertEqual(r.data['scans'][0]['flags'][0]['name'], 'Sus Pect')

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


class FilesPullTests(SimpleTestCase):
    """Time Doctor's /files caps the RESPONSE and shares that cap across every
    user named in one call, so asking for several people silently returns a
    fraction of each one's day (measured 21-Sep-2026 on one staff day: 187 shots
    solo, 37 in a batch of 10, 5 across the whole roster — all HTTP 200, no
    error). The sweep
    must therefore ask for exactly one person per call."""

    def test_one_call_per_user_so_nothing_is_truncated(self):
        from integrations.management.commands.detect_frozen_screen import _pull_files

        calls = []

        class FakeClient:
            def files(self, d_from, d_to, user_ids=None):
                calls.append(list(user_ids or []))
                return [{'userId': user_ids[0], 'numbers': []}]

        ids = [f'u{i}' for i in range(25)]
        _pull_files(FakeClient(), 'from', 'to', ids)

        self.assertEqual(len(calls), 25, 'one call per person, never a batch')
        self.assertTrue(all(len(c) == 1 for c in calls),
                        f'every call must name exactly ONE user, got {calls[:3]}')
        self.assertEqual([c[0] for c in calls], ids, 'every person must be pulled')
