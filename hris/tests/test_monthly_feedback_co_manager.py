"""The monthly feedback employee list must include co-reviewed people.

Arjun (COO) opened /hris/monthly-feedback on 2026-08-07 to comment on three
people and found one. Two of them report elsewhere on paper — that is what
`co_manager` is for — but this page was the only manager surface in HRIS that
ignored the field, so they were simply absent from his list with no explanation.

Synthetic people only.
"""
from __future__ import annotations

import itertools

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from hris.models import HRISProfile
from payroll.models import Employee

U = get_user_model()


_seq = itertools.count(1)


def _employee(name, email, company=None):
    return Employee.objects.create(
        full_name=name, email=email, status=Employee.Status.ACTIVE,
        employee_number=f'TST{next(_seq):04d}',
        **({'company': company} if company else {}),
    )


@override_settings(ELRA_PERF_ENABLED=True)
class MonthlyFeedbackReportListTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.coo_user = U.objects.create_user(
            username='coo', email='coo@alphadirect.co.bw', password='x')
        cls.ceo_user = U.objects.create_user(
            username='ceo', email='ceo@alphadirect.co.bw', password='x')

        cls.coo = _employee('Test COO', 'coo@alphadirect.co.bw')
        cls.ceo = _employee('Test CEO', 'ceo@alphadirect.co.bw')

        # Reports directly to the COO.
        cls.direct = _employee('Direct Report', 'direct@alphadirect.co.bw')
        HRISProfile.objects.create(employee=cls.direct, manager=cls.coo)

        # Reports to the CEO on paper, co-reviewed by the COO.
        cls.shared = _employee('Shared Report', 'shared@alphadirect.co.bw')
        HRISProfile.objects.create(
            employee=cls.shared, manager=cls.ceo, co_manager=cls.coo)

        # Nothing to do with the COO at all.
        cls.other = _employee('Someone Else', 'other@alphadirect.co.bw')
        HRISProfile.objects.create(employee=cls.other, manager=cls.ceo)

    def setUp(self):
        self.client = APIClient()

    def _names(self, resp):
        return sorted(r['name'] for r in resp.json().get('employees', []))

    def test_a_co_reviewed_person_appears_on_the_list(self):
        self.client.force_authenticate(self.coo_user)
        resp = self.client.get('/hris/api/performance/monthly/')
        self.assertEqual(resp.status_code, 200)
        names = self._names(resp)
        self.assertIn('Shared Report', names)
        self.assertIn('Direct Report', names)

    def test_someone_elses_report_is_still_not_on_the_list(self):
        self.client.force_authenticate(self.coo_user)
        resp = self.client.get('/hris/api/performance/monthly/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Someone Else', self._names(resp))

    def test_the_line_manager_still_sees_their_own_person(self):
        """Co-review ADDS a voice; it never removes the line manager's."""
        self.client.force_authenticate(self.ceo_user)
        resp = self.client.get('/hris/api/performance/monthly/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Shared Report', self._names(resp))

    def test_a_person_is_listed_once_not_twice(self):
        """Someone both managed and co-managed by the same person must not
        appear twice — the Q-OR join can duplicate without .distinct()."""
        both = _employee('Both Ways', 'both@alphadirect.co.bw')
        HRISProfile.objects.create(employee=both, manager=self.coo, co_manager=self.coo)
        self.client.force_authenticate(self.coo_user)
        resp = self.client.get('/hris/api/performance/monthly/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._names(resp).count('Both Ways'), 1)

    def test_a_terminated_co_reviewed_person_is_excluded(self):
        gone = _employee('Left The Company', 'gone@alphadirect.co.bw')
        gone.status = Employee.Status.TERMINATED
        gone.save(update_fields=['status'])
        HRISProfile.objects.create(employee=gone, manager=self.ceo, co_manager=self.coo)
        self.client.force_authenticate(self.coo_user)
        resp = self.client.get('/hris/api/performance/monthly/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('Left The Company', self._names(resp))
