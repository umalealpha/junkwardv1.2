"""
hris/tests/test_holiday_off.py — the shared reminder holiday-pause helper.

is_holiday_off() is the single source of truth the reminder crons use to stay
quiet on Botswana non-working public holidays and catch up the next working day
(CFO directive 2026-07-19). Uses far-future dates so it never collides with the
Botswana calendar seeded by the data migration.
"""
import datetime

from django.test import TestCase

from hris.models import PublicHoliday
from hris.workforce_brief import is_holiday_off


class HolidayOffTest(TestCase):

    OFF = datetime.date(2099, 7, 20)       # non-working public holiday
    WORKING_HOL = datetime.date(2099, 7, 22)  # a holiday staff DO work
    NORMAL = datetime.date(2099, 7, 23)    # ordinary day

    @classmethod
    def setUpTestData(cls):
        PublicHoliday.objects.create(
            country_code='BW', holiday_date=cls.OFF, name='Test Day Off',
            is_active=True, is_working_day=False)
        PublicHoliday.objects.create(
            country_code='BW', holiday_date=cls.WORKING_HOL, name='Test Working Holiday',
            is_active=True, is_working_day=True)

    def test_non_working_holiday_is_off(self):
        self.assertTrue(is_holiday_off(self.OFF))

    def test_working_holiday_is_not_off(self):
        # Staff work on this holiday → reminders should still run.
        self.assertFalse(is_holiday_off(self.WORKING_HOL))

    def test_normal_day_is_not_off(self):
        self.assertFalse(is_holiday_off(self.NORMAL))

    def test_inactive_holiday_is_not_off(self):
        PublicHoliday.objects.filter(holiday_date=self.OFF).update(is_active=False)
        self.assertFalse(is_holiday_off(self.OFF))
