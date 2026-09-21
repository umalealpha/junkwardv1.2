"""hris/tests/test_hours_readers_cleanup.py

The three leftovers from the 9-Sep-2026 hours work, approved by the CFO.

  1. A RISK INTRODUCED THAT DAY. `leave_excuse_service.build_day` was changed to
     ask Time Doctor LIVE so a late-synced day is not read as a shortfall — but
     that call sits inside a page load. A Time Doctor FAILURE degrades to the
     stored snapshot; a Time Doctor HANG would stall the page (its client allows
     45 s per call, three calls deep). The feed changes at most once a day, so
     the view now holds the answer for ten minutes.

  2. TWO SCREENS STILL UNDER-REPORTING. The manager/HR Time Doctor report and the
     Telegram hours lookup rendered snapshot rows straight out, so both could
     show a person fewer hours than Omni already held for them. Both now go
     through `hours_for_day.floored_rows`.

  3. A SECOND COPY OF THE SAME LOGIC. The /my-omni hours tile had its own inline
     max(snapshot, record) — right answer, wrong shape, exactly what checklist
     L27 exists to stop. It now calls `hours_for_day.record_hours_by_date`.

Needs Postgres (the omni suite dies on sqlite at ledger migration 0022).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from hris.hours_for_day import floored_rows, record_hours_by_date
from hris.models import HRISProfile, WorkdayJustification
from integrations.models import TimeDoctorDailySnapshot, TimeDoctorUserMap
from payroll.models import Employee

DAY = datetime.date(2026, 9, 8)
SNAPSHOT_HOURS = 1.74      # what the 06:30 pull captured
RECORD_HOURS = 3.24        # what the record holds after the correction


def _member(uid, name, email, tracked):
    return {'user_id': uid, 'name': name, 'email': email,
            'hours_tracked': tracked, 'productive_hours': tracked,
            'productive_pct': 100.0, 'tracked_today': True}


class FlooredRowsTests(TestCase):
    """Fix 2 — the row-shaped floor behind both screens."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='FR-001', full_name='Row Worker',
            email='row.worker@alphadirect.co.bw', status='active',
            department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        TimeDoctorUserMap.objects.create(
            td_user_id='u-row', td_name='Row Worker',
            td_email='row.worker@alphadirect.co.bw',
            employee=cls.emp, confirmed=True)
        WorkdayJustification.objects.create(
            profile=cls.profile, work_date=DAY, required_hours=Decimal('6.50'),
            tracked_hours=Decimal(str(RECORD_HOURS)), status='explained')

    def _rows(self, extra=None):
        rows = [_member('u-row', 'Row Worker', 'row.worker@alphadirect.co.bw',
                        SNAPSHOT_HOURS)]
        if extra:
            rows.append(extra)
        return rows

    def test_a_row_below_the_record_is_raised(self):
        """THE BUG: the screen rendered 1.74 h while Omni held 3.24 h."""
        out = floored_rows(DAY, self._rows(), employees=[self.emp])
        self.assertEqual(float(out[0]['hours_tracked']), RECORD_HOURS)

    def test_a_row_above_the_record_is_left_alone(self):
        rows = [_member('u-row', 'Row Worker', 'row.worker@alphadirect.co.bw', 8.0)]
        out = floored_rows(DAY, rows, employees=[self.emp])
        self.assertEqual(float(out[0]['hours_tracked']), 8.0)

    def test_an_unmatched_row_passes_through_untouched(self):
        """An unmatched Time Doctor account is not evidence of anything, and
        inventing a figure for it would be the name-guessing L19 forbids."""
        stranger = _member('u-nobody', 'Not In Payroll', 'nobody@example.com', 0.5)
        out = floored_rows(DAY, self._rows(stranger), employees=[self.emp])
        got = next(r for r in out if r['user_id'] == 'u-nobody')
        self.assertEqual(float(got['hours_tracked']), 0.5)

    def test_the_stored_payload_is_never_mutated(self):
        """These rows come straight off the snapshot row; raising them in place
        would quietly rewrite what everything else reads."""
        rows = self._rows()
        floored_rows(DAY, rows, employees=[self.emp])
        self.assertEqual(float(rows[0]['hours_tracked']), SNAPSHOT_HOURS)

    def test_empty_input_is_returned_untouched(self):
        self.assertEqual(floored_rows(DAY, []), [])


class RecordHoursByDateTests(TestCase):
    """Fix 3 — the per-date record read behind the /my-omni tile."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='RD-001', full_name='Tile Worker',
            email='tile.worker@alphadirect.co.bw', status='active',
            department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        cls.other = Employee.objects.create(
            employee_number='RD-002', full_name='Someone Else',
            email='someone.else@alphadirect.co.bw', status='active',
            department='Compliance')
        HRISProfile.objects.create(employee=cls.other)

    def test_it_returns_the_recorded_hours_for_the_window(self):
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=DAY, required_hours=Decimal('6.50'),
            tracked_hours=Decimal(str(RECORD_HOURS)), status='explained')
        out = record_hours_by_date(self.emp, DAY, DAY)
        self.assertEqual(out, {DAY: RECORD_HOURS})

    def test_it_is_own_scoped(self):
        """The tile is a person's own page — another employee's hours must never
        appear in it."""
        WorkdayJustification.objects.create(
            profile=HRISProfile.objects.get(employee=self.other), work_date=DAY,
            required_hours=Decimal('6.50'), tracked_hours=Decimal('9.99'),
            status='met')
        out = record_hours_by_date(self.emp, DAY, DAY)
        self.assertEqual(out, {})

    def test_days_outside_the_window_are_excluded(self):
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=DAY - datetime.timedelta(days=10),
            required_hours=Decimal('6.50'), tracked_hours=Decimal('5.00'),
            status='met')
        self.assertEqual(record_hours_by_date(self.emp, DAY, DAY), {})


class ExcusesFeedCacheTests(TestCase):
    """Fix 1 — cache the slow Time Doctor call, and ONLY that.

    The first version of this cached the whole built day, which froze the
    dashboard's own decisions: the page reloads this endpoint straight after
    Accept/Reject, so the row kept reading "not accepted" for ten minutes while
    the toast said otherwise (Fable review 2026-09-09). Caught before it shipped.
    """

    URL = '/hris/api/leave-excuse/'

    @classmethod
    def setUpTestData(cls):
        from core.models import User
        cls.user = User.objects.create_superuser(
            username='excuse.viewer', email='excuse.viewer@alphadirect.co.bw',
            password='x')

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY, payload=[])

    def tearDown(self):
        cache.clear()

    def test_two_page_loads_ask_time_doctor_once(self):
        """THE RISK: the live read sits inside the request (45 s per call, three
        calls deep), so without a cache a Time Doctor hang stalls the page."""
        with mock.patch('hris.hours_for_day.live',
                        return_value=([], 'live')) as live:
            r1 = self.client.get(self.URL, {'date': DAY.isoformat(), 'no_ai': '1'})
            r2 = self.client.get(self.URL, {'date': DAY.isoformat(), 'no_ai': '1'})
        self.assertEqual(r1.status_code, 200, r1.content[:300])
        self.assertEqual(r2.status_code, 200, r2.content[:300])
        self.assertEqual(
            live.call_count, 1,
            'Time Doctor was asked again on the second page load')

    def test_a_different_day_is_not_served_the_cached_one(self):
        other = DAY - datetime.timedelta(days=1)
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=other, payload=[])
        with mock.patch('hris.hours_for_day.live',
                        return_value=([], 'live')) as live:
            self.client.get(self.URL, {'date': DAY.isoformat(), 'no_ai': '1'})
            self.client.get(self.URL, {'date': other.isoformat(), 'no_ai': '1'})
        self.assertEqual(live.call_count, 2)

    def test_a_decision_shows_on_the_very_next_load(self):
        """THE REGRESSION THE WHOLE-DAY CACHE WOULD HAVE SHIPPED. HR clicks
        Accept, the page reloads this endpoint, and the row must already read
        accepted — not the ten-minute-old copy."""
        emp = Employee.objects.create(
            employee_number='EC-001', full_name='Decided Worker',
            email='decided.worker@alphadirect.co.bw', status='active',
            department='Compliance')
        prof = HRISProfile.objects.create(employee=emp)
        from hris.models import TrackingDirective
        TrackingDirective.objects.create(employee=emp, expected_to_track=True)
        wj = WorkdayJustification.objects.create(
            profile=prof, work_date=DAY, required_hours=Decimal('6.50'),
            tracked_hours=Decimal('0.00'), status='unjustified',
            justification='Power was out all morning.')

        with mock.patch('hris.hours_for_day.live', return_value=([], 'live')):
            before = self.client.get(
                self.URL, {'date': DAY.isoformat(), 'no_ai': '1'}).json()
            row = next((r for r in before['rows']
                        if r.get('employee') == 'Decided Worker'), None)
            self.assertIsNotNone(row, before)

            # HR accepts it — the same write the dashboard's Accept button makes.
            wj.status = 'justified'
            wj.reviewed_by = self.user
            wj.save(update_fields=['status', 'reviewed_by'])

            after = self.client.get(
                self.URL, {'date': DAY.isoformat(), 'no_ai': '1'}).json()

        row2 = next((r for r in after['rows']
                     if r.get('employee') == 'Decided Worker'), None)
        self.assertIsNotNone(row2, after)
        self.assertEqual(
            row2['status'], 'accepted',
            'the decision did not show on the next load — a cached day would '
            'leave HR staring at "not accepted" for ten minutes')


class TelegramAmbiguousNameTests(TestCase):
    """L19 — the chat lookup must never pick between two people for you.

    Raised by the review panel. Flooring fixed WHICH FIGURE a row carries (it is
    resolved through the confirmed Time Doctor account map). This is the other
    half: WHICH PERSON the question resolves to. Typing "chris" used to read out
    whichever Christopher the loop reached first, as fact, into a chat — the
    literal Modiri/Christopher case L19 was written for.
    """

    @classmethod
    def setUpTestData(cls):
        for n, (num, name, email) in enumerate((
                ('TG-1', 'Christopher Kelefatse', 'ckelefatse@alphadirect.co.bw'),
                ('TG-2', 'Christopher Moeng', 'cmoeng@alphadirect.co.bw'))):
            emp = Employee.objects.create(
                employee_number=num, full_name=name, email=email,
                status='active', department='Claims')
            HRISProfile.objects.create(employee=emp)
            TimeDoctorUserMap.objects.create(
                td_user_id=f'u-tg{n}', td_name=name, td_email=email,
                employee=emp, confirmed=True)

    def setUp(self):
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY, payload=[
                _member('u-tg0', 'Christopher Kelefatse',
                        'ckelefatse@alphadirect.co.bw', 7.5),
                _member('u-tg1', 'Christopher Moeng',
                        'cmoeng@alphadirect.co.bw', 1.0),
            ])

    def test_an_ambiguous_name_asks_instead_of_guessing(self):
        """THE BUG: this answered for whichever Christopher came first."""
        from core.telegram_bot.services import time_doctor_query
        out = time_doctor_query('christopher')
        self.assertIn('matches 2 people', out)
        self.assertIn('Christopher Kelefatse', out)
        self.assertIn('Christopher Moeng', out)
        self.assertNotIn('Hours tracked', out,
                         'it answered with one person\'s hours anyway')

    def test_an_unambiguous_name_still_answers(self):
        """The guard must not make the lookup useless."""
        from core.telegram_bot.services import time_doctor_query
        out = time_doctor_query('moeng')
        self.assertIn('Christopher Moeng', out)
        self.assertIn('Hours tracked', out)
        self.assertNotIn('matches', out)

    def test_a_name_nobody_has_says_so(self):
        from core.telegram_bot.services import time_doctor_query
        self.assertIn('No one on the staff list', time_doctor_query('nobody here'))

    def test_the_hr_name_finds_them_even_when_time_doctor_spells_it_differently(self):
        """THE OTHER HALF OF L19, raised by the panel. Searching Time Doctor's own
        text meant that asking for someone by the name HR knows them by returned
        'no record' when Time Doctor spelled it differently — the literal Modiri
        Katai / 'Modiri Mokati' case, corrected about twenty times."""
        emp = Employee.objects.create(
            employee_number='TG-3', full_name='Modiri Fofo Katai',
            email='mkatai@alphadirect.co.bw', status='active', department='Ops')
        HRISProfile.objects.create(employee=emp)
        TimeDoctorUserMap.objects.create(
            td_user_id='u-tg9', td_name='Modiri Mokati',      # DIFFERENT surname
            td_email='mkatai@alphadirect.co.bw', employee=emp, confirmed=True)
        TimeDoctorDailySnapshot.objects.filter(as_of=DAY).delete()
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY,
            payload=[_member('u-tg9', 'Modiri Mokati',
                             'mkatai@alphadirect.co.bw', 3.07)])
        from core.telegram_bot.services import time_doctor_query
        out = time_doctor_query('Modiri Katai')          # her HR name
        self.assertIn('Modiri Fofo Katai', out)
        self.assertIn('3.07h', out)

    def test_a_full_name_answers_instead_of_asking(self):
        """Under a flat any-token match a FULL name hit both Christophers and
        the bot replied "ask again with the full name" — which the user had just
        done (Fable review 2026-09-09)."""
        from core.telegram_bot.services import time_doctor_query
        out = time_doctor_query('Christopher Moeng')
        self.assertIn('Christopher Moeng', out)
        self.assertIn('Hours tracked', out)
        self.assertNotIn('matches', out)

    def test_it_refuses_to_quote_hours_if_the_record_cannot_be_read(self):
        """Unlike the read-only screens, this prints a figure about a NAMED
        person into a chat as fact, so it fails closed rather than quoting a
        number that may be too low."""
        from core.telegram_bot.services import time_doctor_query
        with mock.patch('hris.models.WorkdayJustification.objects.filter',
                        side_effect=RuntimeError('database gone')):
            out = time_doctor_query('moeng')
        self.assertIn('could not be read', out)
        self.assertNotIn('Hours tracked', out)

    def test_a_matched_person_on_a_zero_day_is_not_told_their_link_is_missing(self):
        """Fable review 2026-09-09 (checklist L29). The best-so-far loop was
        seeded with None and tested `got > (hrs or 0.0)`, so a legitimate 0.00 h
        never beat the seed and the answer fell through to the no-account
        branch. A correctly-linked employee who simply did not track that day
        was told to go and confirm a link that was already fine."""
        from core.telegram_bot.services import time_doctor_query
        TimeDoctorDailySnapshot.objects.filter(as_of=DAY).delete()
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY, payload=[
                _member('u-tg1', 'Christopher Moeng',
                        'cmoeng@alphadirect.co.bw', 0.0),
            ])
        out = time_doctor_query('moeng')
        self.assertIn('Hours tracked', out, out)
        self.assertIn('0.0', out)
        self.assertNotIn(
            'no matched Time Doctor account', out,
            'a matched person on a zero day was reported as having no link')

    def test_two_people_on_identical_hours_each_get_their_own_figures(self):
        """Fable review 2026-09-09 (checklist L30). The presentation extras were
        picked by matching on the VALUE of hours_tracked, so when two staff share
        a figure the reply showed one person's productive % and last-seen under
        the other person's name. 0.0 is the commonest shared value, so once the
        zero-day fix landed this would have hit constantly."""
        from core.telegram_bot.services import time_doctor_query
        TimeDoctorDailySnapshot.objects.filter(as_of=DAY).delete()
        a = _member('u-tg0', 'Christopher Kelefatse',
                    'ckelefatse@alphadirect.co.bw', 7.5)
        a['productive_pct'] = 91.0
        a['last_seen'] = '2026-09-08T17:45'
        b = _member('u-tg1', 'Christopher Moeng',
                    'cmoeng@alphadirect.co.bw', 7.5)
        b['productive_pct'] = 42.0
        b['last_seen'] = '2026-09-08T11:05'
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY, payload=[a, b])

        out = time_doctor_query('moeng')
        self.assertIn('42', out,
                      "it showed the OTHER person's productive % under this name")
        self.assertNotIn('91', out)
        self.assertIn('11:05', out)
        self.assertNotIn('17:45', out)



# 192.0.2.x is RFC 5737's documentation range, deliberately NOT the address
# prod actually leaked: committing the real internal IP into the repo is the
# same disclosure the fix exists to stop (Opus adjudication, 2026-09-15).
class TelegramLastSeenRenderTests(TestCase):
    """The Time Doctor feed does not send `last_seen` as a string. In real prod
    data it is the raw API object, and the reply interpolated the whole dict:

        Last seen: {'ip': '192.0.2.7', 'online': False,
                    'updatedAt': '2026-09-08T09:39:54.706Z'}

    Verified live on prod 2026-09-09. Unreadable, and it puts an internal IP
    address into a chat message. Only the moment is wanted, in Botswana time.
    """

    def setUp(self):
        self.emp = Employee.objects.create(
            employee_number='LS-1', full_name='Lesego Seen',
            email='lseen@alphadirect.co.bw', status='active', department='Ops')
        HRISProfile.objects.create(employee=self.emp)
        TimeDoctorUserMap.objects.create(
            td_user_id='u-ls1', td_name='Lesego Seen',
            td_email='lseen@alphadirect.co.bw', employee=self.emp,
            confirmed=True)
        TimeDoctorDailySnapshot.objects.filter(as_of=DAY).delete()

    def _reply(self, last_seen):
        from core.telegram_bot.services import time_doctor_query
        m = _member('u-ls1', 'Lesego Seen', 'lseen@alphadirect.co.bw', 6.0)
        m['last_seen'] = last_seen
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY, payload=[m])
        out = time_doctor_query('Lesego Seen')
        # Guard against a false green: every assertion below is about the
        # Last-seen line, so the reply must actually BE the person's card.
        assert 'Hours tracked' in out, out
        return out

    def test_the_real_dict_shape_never_leaks_the_ip_address(self):
        out = self._reply({'ip': '192.0.2.7', 'online': False,
                           'updatedAt': '2026-09-08T09:39:54.706Z'})
        self.assertNotIn('192.0.2.7', out,
                         'an internal IP address was rendered into the reply')
        self.assertNotIn('ip', out.split('Last seen:')[-1].lower())
        self.assertNotIn('online', out.lower())

    def test_the_real_dict_shape_is_shown_as_a_botswana_time_moment(self):
        """09:39:54Z is 11:39 in Botswana (UTC+2)."""
        out = self._reply({'ip': '192.0.2.7', 'online': False,
                           'updatedAt': '2026-09-08T09:39:54.706Z'})
        self.assertIn('Last seen: 2026-09-08 11:39 SAST', out, out)

    def test_a_dict_with_no_timestamp_says_nothing_at_all(self):
        out = self._reply({'ip': '192.0.2.7', 'online': False})
        self.assertNotIn('Last seen', out, out)
        self.assertNotIn('192.0.2.7', out)

    def test_a_plain_string_still_renders_and_is_not_shifted(self):
        """A naive timestamp carries no zone, so it must not be moved two
        hours. The existing snapshot fixtures use this shape."""
        out = self._reply('2026-09-08T11:05')
        self.assertIn('11:05', out, out)
        self.assertNotIn('13:05', out)

    def test_an_unreadable_string_is_passed_through_unchanged(self):
        out = self._reply('sometime yesterday')
        self.assertIn('sometime yesterday', out, out)
