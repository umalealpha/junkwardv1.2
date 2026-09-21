"""H3 — one-click convert an APPROVED Authority to Recruit into an Employee.

CFO 2026-08-26. The button only works once all five have signed; it creates the
payroll Employee + starts onboarding, does NOT create a login, and is idempotent.
Without the convert endpoint these tests 404 / fail — they pin the behaviour.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company
from payroll.models import Employee
from recruitment.models import AuthorityToRecruit

CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'   # a signatory → can_view/can_sign


def _approved_atr(**kw):
    base = dict(
        kind=AuthorityToRecruit.Kind.RECRUIT,
        person_name='Naledi Phiri',
        position='Claims Officer',
        department='Claims',
        entity='Alpha Direct Insurance Company',
        quoted_ctc_monthly=Decimal('20000'),
    )
    base.update(kw)
    a = AuthorityToRecruit.objects.create(**base)
    # Sign all five so it is APPROVED.
    a.approvals = {slug: {'decision': 'approved', 'by': label, 'at': '2026-08-26T00:00:00Z'}
                   for slug, label, _e in AuthorityToRecruit.SIGNATORIES}
    a.recompute_status()
    a.save(update_fields=['approvals', 'status', 'updated_at'])
    return a


class ConvertToEmployeeTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('cfo', email=CFO_EMAIL, password='x')
        self.client.force_authenticate(self.cfo)
        self.company = Company.objects.create(code='ADI',
                                              name='Alpha Direct Insurance Company')

    def _url(self, a):
        return reverse('v1-recruitment-authority-convert', args=[a.id])

    def test_pending_authority_cannot_be_converted(self):
        a = AuthorityToRecruit.objects.create(person_name='X', position='Y',
                                              entity='Alpha Direct Insurance Company')
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 400)
        self.assertIn('approve', r.json()['detail'].lower())

    def test_approved_authority_creates_employee_and_onboarding(self):
        a = _approved_atr()
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 201, r.content)
        emp = Employee.objects.filter(full_name='Naledi Phiri').first()
        self.assertIsNotNone(emp)
        self.assertEqual(emp.company_id, self.company.id)
        self.assertFalse(r.json()['login_created'])           # IT makes the login
        self.assertTrue(r.json()['onboarding_tasks_created'] >= 3)  # 30-60-90 + essentials
        self.assertTrue(hasattr(emp, 'hris_profile'))         # talent profile shell
        a.refresh_from_db()
        self.assertEqual(a.converted_employee_id, emp.id)

    def test_convert_is_idempotent(self):
        a = _approved_atr()
        first = self.client.post(self._url(a))
        self.assertEqual(first.status_code, 201)
        second = self.client.post(self._url(a))
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['already'])
        # Only one employee, never a duplicate shell.
        self.assertEqual(Employee.objects.filter(full_name='Naledi Phiri').count(), 1)

    def test_unknown_entity_is_refused_not_guessed(self):
        a = _approved_atr(entity='Some Company That Does Not Exist')
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 400)
        self.assertIn('does not match', r.json()['detail'].lower())
        self.assertFalse(Employee.objects.filter(full_name='Naledi Phiri').exists())

    def test_namesake_in_another_entity_is_refused_not_linked(self):
        """A same-name active employee under a DIFFERENT company must not be
        silently absorbed as this hire (Fable H90) — refuse, name them."""
        other = Company.objects.create(code='UNI', name='Unicoin Pty Ltd')
        Employee.objects.create(full_name='Naledi Phiri', company=other,
                                employee_number='UNI-EXISTING',
                                status=Employee.Status.ACTIVE)
        a = _approved_atr()  # entity = Alpha Direct Insurance Company (self.company)
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 400)
        self.assertIn('transfer', r.json()['detail'].lower())
        # No new ADI employee minted, authority left unconverted.
        self.assertEqual(Employee.objects.filter(full_name='Naledi Phiri').count(), 1)
        a.refresh_from_db()
        self.assertIsNone(a.converted_employee_id)


class ScorecardTests(APITestCase):
    """H2 — interview scorecards (HR-gated; one card per interviewer per round)."""
    def setUp(self):
        # HR access comes via superuser here (user_can_access_hris otherwise).
        self.hr = User.objects.create_superuser('hr', email='hr@alphadirect.co.bw', password='x')
        self.client.force_authenticate(self.hr)
        from recruitment.models import Application, Candidate, JobRequisition
        req = JobRequisition.objects.create(title='Actuary')
        cand = Candidate.objects.create(full_name='Kabo Rammidi', email='k@example.com')
        self.app = Application.objects.create(requisition=req, candidate=cand)

    def _url(self):
        return reverse('v1-recruitment-scorecards', args=[self.app.id])

    def test_file_and_relist_scorecard(self):
        r = self.client.post(self._url(), {'round': 'interview_1', 'score': 82,
                             'recommendation': 'yes', 'strengths': 'Strong SQL'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['score'], 82)
        listing = self.client.get(self._url())
        self.assertEqual(listing.json()['count'], 1)

    def test_resubmit_updates_not_duplicates(self):
        self.client.post(self._url(), {'round': 'interview_1', 'score': 50,
                         'recommendation': 'maybe'}, format='json')
        self.client.post(self._url(), {'round': 'interview_1', 'score': 90,
                         'recommendation': 'strong_yes'}, format='json')
        listing = self.client.get(self._url()).json()
        self.assertEqual(listing['count'], 1)                    # same round → updated
        self.assertEqual(listing['scorecards'][0]['score'], 90)
