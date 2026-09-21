"""Config + email-builder tests for the intraday Time Doctor hours reminders
(CFO 2026-08-17). Pins the thresholds/audience so they can't silently drift, and
checks the pure HTML builders — no DB / no Time Doctor needed.
"""
from django.test import SimpleTestCase

from hris.management.commands.send_hours_reminder import _email_html, SLOTS
from hris.management.commands.send_weekly_hours_review import _table_html, WEEKDAY_TARGET


class HoursReminderConfigTests(SimpleTestCase):
    def test_slot_thresholds_and_audience(self):
        # The two daily nudges: 11:45 under 2h (staff only), 16:00 under 4h
        # (staff + managers). These are the CFO-agreed numbers — pin them.
        self.assertEqual(SLOTS['morning']['threshold'], 2.0)
        self.assertTrue(SLOTS['morning']['exclude_managers'])
        self.assertTrue(SLOTS['morning']['arrival_line'])
        self.assertEqual(SLOTS['afternoon']['threshold'], 4.0)
        self.assertFalse(SLOTS['afternoon']['exclude_managers'])
        self.assertTrue(SLOTS['afternoon']['escalate'])

    def test_email_shows_hours_threshold_and_button(self):
        html = _email_html('Kabo', 'Monday, 18 August 2026',
                           'https://omni.alphadirect.co.bw/api/leave-explain/tok/',
                           threshold=2.0, hours=0.5, when='11:45')
        self.assertIn('Explain my day', html)
        self.assertIn('0.5 hours', html)
        self.assertIn('2 hours', html)          # threshold rendered
        self.assertIn('11:45', html)
        # No late-start line unless one is supplied.
        self.assertNotIn('start at', html)

    def test_email_adds_late_start_line(self):
        html = _email_html('Kabo', 'Monday, 18 August 2026', 'https://x/y/',
                           threshold=2.0, hours=1.0, when='11:45', late_start='08:41')
        self.assertIn('08:41', html)
        self.assertIn('8:00 am', html)


class WeeklyReviewTests(SimpleTestCase):
    def test_weekday_target_is_six_and_a_half(self):
        self.assertEqual(WEEKDAY_TARGET, 6.5)

    def test_table_shows_gap_today_marker_and_prompt(self):
        # week_rows = (label, hours, is_today). Gap is computed by the caller on
        # COMPLETED days only and passed in; today (Wed here) is shown "(so far)".
        rows = [('Mon 18 Aug', 6.5, False), ('Tue 19 Aug', 3.0, False), ('Wed 20 Aug', 0.0, True)]
        html = _table_html('Kabo', rows, total_so_far=9.5, to_date_target=13.0, gap=3.5,
                           link='https://omni.alphadirect.co.bw/api/leave-explain/tok/')
        self.assertIn('Record my catch-up plan', html)
        self.assertIn('9.5h', html)              # total so far
        self.assertIn('3.5 hours', html)          # the gap, measured on completed days
        self.assertIn('short', html)
        self.assertIn('Saturday', html)           # catch-up-by-Saturday ask
        self.assertIn('(so far)', html)           # today marked partial, not a shortfall

    def test_table_on_target_has_no_gap_prompt(self):
        rows = [('Mon 18 Aug', 6.5, False), ('Tue 19 Aug', 6.5, False)]
        html = _table_html('Kabo', rows, total_so_far=13.0, to_date_target=13.0, gap=0.0, link='https://x/y/')
        self.assertIn('on target', html)
        self.assertNotIn('short', html)


class ExecTitleTests(SimpleTestCase):
    def test_no_tracker_titles_are_only_c_suite(self):
        # Fix 1 root cause: no_tracker_title(p) returns a TITLE STRING, so the
        # reminders test `no_tracker_title(p) in NO_TRACKER_TITLES`, never truthily.
        # Pin the set so an ordinary title is never mistaken for a non-tracking exec.
        from hris.workforce_roles import NO_TRACKER_TITLES
        self.assertIn('cfo', NO_TRACKER_TITLES)
        self.assertIn('ceo', NO_TRACKER_TITLES)
        self.assertIn('coo', NO_TRACKER_TITLES)
        self.assertNotIn('accountant', NO_TRACKER_TITLES)
        self.assertNotIn('claims manager', NO_TRACKER_TITLES)
