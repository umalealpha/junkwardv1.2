"""Tests for Career Tracks — promotion-readiness records (CFO 2026-07-13).

Covers the access gates (ess never reaches the surface; HR tier manages),
creation with milestones, and milestone status updates.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Company, UserProfile
from hris.models import CareerMilestone, CareerTrack
from payroll.models import Employee


def _unlock(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    profile.save(update_fields=['hris_unlocked_until'])
    return profile


class CareerTrackTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TEST', name='Test Co.')
        cls.emp = Employee.objects.create(
            employee_number='E100', full_name='Meduduetso Tlagae',
            job_title='Senior Associate', department='Health',
            email='mtlagae@test.example', company=cls.company)
        cls.admin = User.objects.create_superuser(
            'boss', 'boss@example.com', 'x')
        cls.ess_user = User.objects.create_user('worker', 'w@example.com', 'x')

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_ess_cannot_read(self):
        r = self._client(self.ess_user).get('/hris/api/talent/career-tracks/')
        self.assertEqual(r.status_code, 403)

    def test_hr_create_and_list(self):
        _unlock(self.admin)
        c = self._client(self.admin)
        r = c.post('/hris/api/talent/career-tracks/', {
            'employee_email': 'mtlagae@test.example',
            'target_role': 'Projects Manager',
            'context': 'Secondment record',
            'milestones': [
                'Achieve the Healthcare budgets.',
                'Bring on board all the large hospitals.',
                'Implement the BWP100 Continental Re product.',
            ],
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body['target_role'], 'Projects Manager')
        self.assertEqual(len(body['milestones']), 3)
        self.assertEqual(body['milestones'][0]['status'], 'pending')

        r2 = c.get('/hris/api/talent/career-tracks/')
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertTrue(data['can_manage'])
        self.assertEqual(len(data['tracks']), 1)
        self.assertEqual(data['tracks'][0]['employee']['name'],
                         'Meduduetso Tlagae')

    def test_milestone_update(self):
        _unlock(self.admin)
        track = CareerTrack.objects.create(
            employee=self.emp, target_role='Projects Manager')
        ms = CareerMilestone.objects.create(
            track=track, order=1, description='Achieve the budgets.')
        c = self._client(self.admin)
        r = c.patch(
            f'/hris/api/talent/career-tracks/milestones/{ms.id}/',
            {'status': 'done', 'evidence': 'FY27 Health budget achieved.'},
            format='json')
        self.assertEqual(r.status_code, 200, r.content)
        ms.refresh_from_db()
        self.assertEqual(ms.status, 'done')
        self.assertEqual(ms.evidence, 'FY27 Health budget achieved.')

    def test_bad_status_rejected(self):
        _unlock(self.admin)
        track = CareerTrack.objects.create(
            employee=self.emp, target_role='Projects Manager')
        ms = CareerMilestone.objects.create(
            track=track, order=1, description='Milestone.')
        r = self._client(self.admin).patch(
            f'/hris/api/talent/career-tracks/milestones/{ms.id}/',
            {'status': 'not-a-status'}, format='json')
        self.assertEqual(r.status_code, 400)
