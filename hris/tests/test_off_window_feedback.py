"""Off-window payment raise → a planning concern in the raiser's monthly
performance feedback (CFO 2026-09-02: window abolished, but a negative review
point for not planning the load in time)."""
from __future__ import annotations

import itertools

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from hris import auto_feedback as AF

_SEQ = itertools.count(1)
_TODAY = timezone.localdate()
YEAR, MONTH = _TODAY.year, _TODAY.month


def _emp_with_user(name):
    from payroll.models import Employee
    n = next(_SEQ)
    u = User.objects.create_user(f'loader{n}', password='x')
    e = Employee.objects.create(full_name=name, email='',
                                employee_number=f'W{n:05d}', user=u)
    return e, u


def _profile(emp):
    from hris.models import HRISProfile
    return HRISProfile.objects.create(employee=emp)


def _pr(user, off_window):
    from taskboard.models import PaymentRequest
    n = next(_SEQ)
    return PaymentRequest.objects.create(
        created_by=user, ref=f'PAY-T-{n:06d}', subject='test', summary='test',
        loaded_off_window=off_window)


class OffWindowFeedbackTests(TestCase):
    def test_off_window_loads_surface_in_facts_and_narrative(self):
        emp, user = _emp_with_user('The Loader')
        prof = _profile(emp)
        _pr(user, True)
        _pr(user, True)          # two raised off-window this month
        _pr(user, False)         # one raised on-window — never flagged
        facts = AF.facts_for(prof, YEAR, MONTH)
        self.assertEqual(facts['off_window_loads'], 2)
        self.assertTrue(facts['has_anything_to_say'])
        narrative = AF.draft_narrative('The Loader', facts)
        self.assertIn('outside the morning window', narrative)
        # It states a fact — it must not pretend to be a rating.
        self.assertIn('not the system', narrative.lower())

    def test_clean_month_has_no_off_window_concern(self):
        emp, user = _emp_with_user('Clean Loader')
        prof = _profile(emp)
        _pr(user, False)
        facts = AF.facts_for(prof, YEAR, MONTH)
        self.assertEqual(facts['off_window_loads'], 0)
        self.assertNotIn('outside the morning window',
                         AF.draft_narrative('Clean Loader', facts))

    def test_off_window_load_is_per_person(self):
        emp_a, user_a = _emp_with_user('Loader A')
        emp_b, user_b = _emp_with_user('Loader B')
        _pr(user_a, True)                       # A raised off-window
        facts_b = AF.facts_for(_profile(emp_b), YEAR, MONTH)
        self.assertEqual(facts_b['off_window_loads'], 0)   # not counted against B
