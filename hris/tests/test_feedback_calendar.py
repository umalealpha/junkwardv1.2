"""The feedback cycle's dates and who each stage tells (CFO 2026-09-09).

The old cycle was 5th / 6th / auto-post 7th — a manager got barely a day
between the reminder and the post, and the staff member it was about was never
told at all. 83 write-ups sat unsigned because only the manager knew.
"""
from django.test import SimpleTestCase

from hris.management.commands.monthly_feedback_notice import (
    AUTOPOST_DAY, NOTICE_DAY, REMINDER2_DAY, REMINDER_DAY, Command)
from hris.performance_feedback_models import MonthlyFeedbackNotice as N


class CalendarTests(SimpleTestCase):
    def test_the_cfo_dates(self):
        self.assertEqual((NOTICE_DAY, REMINDER_DAY, REMINDER2_DAY, AUTOPOST_DAY),
                         (2, 4, 7, 10))

    def test_the_manager_gets_a_full_week_before_anything_posts(self):
        """The point of the move: 5th->6th->7th gave one day."""
        self.assertGreaterEqual(AUTOPOST_DAY - NOTICE_DAY, 7)

    def test_there_are_two_reminders_before_the_post(self):
        self.assertLess(NOTICE_DAY, REMINDER_DAY)
        self.assertLess(REMINDER_DAY, REMINDER2_DAY)
        self.assertLess(REMINDER2_DAY, AUTOPOST_DAY)


class StageTests(SimpleTestCase):
    def test_no_flags_is_the_second_of_the_month(self):
        self.assertEqual(Command._stage({}), 'notice')

    def test_reminder_flag(self):
        self.assertEqual(Command._stage({'reminder': True}), 'reminder')

    def test_final_flag(self):
        self.assertEqual(Command._stage({'final': True}), 'final')

    def test_final_wins_over_reminder(self):
        """Both flags set must not silently send the 4th's wording on the 7th."""
        self.assertEqual(Command._stage({'reminder': True, 'final': True}), 'final')

    def test_every_stage_maps_to_its_own_record_kind(self):
        """Two reminders must not collide on one unique (manager, period, kind)
        row — the second would be skipped as 'already sent'."""
        kinds = {'notice': N.Kind.NOTICE, 'reminder': N.Kind.REMINDER,
                 'final': N.Kind.REMINDER2}
        self.assertEqual(len(set(kinds.values())), 3)
        self.assertIn('reminder2', [c for c, _ in N.Kind.choices])


class WhoIsToldTests(SimpleTestCase):
    def test_the_reminders_tell_the_staff_member_too_not_just_the_manager(self):
        """CFO: '4th reminder to staff and manager, 7th another reminder to
        staff and manager'."""
        src = Command._tell_staff.__doc__ or ''
        self.assertTrue(hasattr(Command, '_tell_staff'))
        self.assertIn('manager', src)

    def test_the_staff_email_carries_no_ones_facts(self):
        """The manager's email lists the WHOLE team's hours and shortfalls. The
        staff nudge must say only that theirs is pending and who with — never
        render a draft, or a colleague's figures land in someone's inbox."""
        import inspect
        src = inspect.getsource(Command._tell_staff)
        self.assertNotIn("draft", src)              # the facts are never rendered
        self.assertNotIn("_person_block", src)      # nor the per-person block
        self.assertNotIn("hours_gap", src)

    def test_the_staff_nudge_does_not_cc_exco(self):
        """One line per person per month across 150 staff would bury the EXCO
        inbox; the manager's own email already lists the same people once."""
        import inspect
        self.assertIn('cc_cfo=False', inspect.getsource(Command._tell_staff))

    def test_one_bad_address_does_not_abandon_the_rest(self):
        """The 2026-08-27 lesson: a single SMTP failure mid-loop left earlier
        people written up and later ones untouched."""
        import inspect
        src = inspect.getsource(Command._tell_staff)
        self.assertIn('except Exception', src)
        self.assertIn('continue', src)
