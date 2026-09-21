"""Tests for the Internal Audit module — the enforced controls + independence.

Focus: the rules the spec says must be structural, not left to discipline:
  - five mandatory finding elements
  - forced root-cause taxonomy ('Other' needs justification)
  - rating auto-computed from likelihood x impact (never typed)
  - management response required before 'agreed'
  - follow-up needs evidence to close as Implemented; overdue is derived
  - independence: only the Internal Audit roster may write; execs are view-only
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from internal_audit.models import Engagement, Finding, FollowUp, ManagementResponse
from internal_audit.rating import compute_rating


def _valid_finding_kwargs(engagement, **over):
    base = dict(
        engagement=engagement,
        title='Bank recs not independently reviewed',
        criteria='Policy FIN-03 requires monthly independent bank rec review.',
        condition='3 of 6 months had no reviewer sign-off.',
        evidence_reference='WP-04',
        root_cause=Finding.RootCause.MISSING_CONTROL,
        effect_category=Finding.EffectCategory.OPERATIONAL,
        effect_detail='Unreviewed recs; error/fraud could go undetected.',
        likelihood=4,
        impact=3,
        rating_justification='Recurring gap across half the period.',
    )
    base.update(over)
    return base


class RatingTests(TestCase):
    def test_matrix_bands(self):
        self.assertEqual(compute_rating(1, 1), 'low')       # 1
        self.assertEqual(compute_rating(2, 2), 'low')       # 4
        self.assertEqual(compute_rating(2, 3), 'medium')    # 6
        self.assertEqual(compute_rating(3, 3), 'medium')    # 9 -> top of medium band
        self.assertEqual(compute_rating(2, 5), 'high')      # 10 -> into high
        self.assertEqual(compute_rating(3, 4), 'high')      # 12
        self.assertEqual(compute_rating(5, 5), 'critical')  # 25

    def test_rating_is_computed_on_save_not_trusted(self):
        eng = Engagement.objects.create(reference='IA-2026-001', title='Finance controls')
        f = Finding(**_valid_finding_kwargs(eng, likelihood=5, impact=5, rating='low'))
        f.save()
        self.assertEqual(f.rating, 'critical')  # ignored the typed 'low'


class FindingEnforcementTests(TestCase):
    def setUp(self):
        self.eng = Engagement.objects.create(reference='IA-2026-002', title='Claims')

    def test_missing_element_blocks_save(self):
        for field in ('criteria', 'condition', 'effect_detail', 'rating_justification'):
            f = Finding(**_valid_finding_kwargs(self.eng, **{field: '   '}))
            with self.assertRaises(ValidationError) as ctx:
                f.full_clean()
            self.assertIn(field, ctx.exception.message_dict)

    def test_condition_needs_evidence(self):
        f = Finding(**_valid_finding_kwargs(self.eng, evidence_reference=''))
        with self.assertRaises(ValidationError) as ctx:
            f.full_clean()
        self.assertIn('evidence_reference', ctx.exception.message_dict)

    def test_root_cause_other_requires_justification(self):
        f = Finding(**_valid_finding_kwargs(self.eng, root_cause=Finding.RootCause.OTHER,
                                            root_cause_justification=''))
        with self.assertRaises(ValidationError) as ctx:
            f.full_clean()
        self.assertIn('root_cause_justification', ctx.exception.message_dict)

    def test_human_error_not_a_choice(self):
        values = {c[0] for c in Finding.RootCause.choices}
        self.assertNotIn('human_error', values)
        self.assertNotIn('oversight', values)

    def test_fraud_category_sets_flag(self):
        f = Finding(**_valid_finding_kwargs(self.eng, effect_category=Finding.EffectCategory.FRAUD))
        f.save()
        self.assertTrue(f.fraud_flag)

    def test_cannot_agree_without_agreed_response(self):
        f = Finding(**_valid_finding_kwargs(self.eng))
        f.save()
        f.status = Finding.Status.AGREED
        with self.assertRaises(ValidationError):
            f.full_clean()
        # attach agreed response -> now valid
        ManagementResponse.objects.create(
            finding=f, response_text='Will implement reviewer sign-off.',
            action='Add sign-off step', owner='Finance Manager',
            target_date=timezone.now().date() + dt.timedelta(days=30), agreed=True)
        f.refresh_from_db()
        f.status = Finding.Status.AGREED
        f.full_clean()  # no raise


class FollowUpTests(TestCase):
    def setUp(self):
        self.eng = Engagement.objects.create(reference='IA-2026-003', title='IT')
        self.f = Finding(**_valid_finding_kwargs(self.eng, likelihood=5, impact=5))
        self.f.save()

    def test_implemented_needs_evidence(self):
        fu = FollowUp(finding=self.f, status=FollowUp.Status.IMPLEMENTED, evidence_reference='')
        with self.assertRaises(ValidationError):
            fu.full_clean()

    def test_retest_date_defaults_from_rating(self):
        fu = FollowUp(finding=self.f)  # critical -> 90 days
        fu.save()
        # localdate, not now().date(): FollowUp.save() defaults the re-test date
        # off timezone.localdate() (Africa/Gaborone). Between 22:00 and midnight
        # UTC the two clocks are a day apart, so a UTC-based expectation turned
        # this test red every night on CI (2026-09-10, 22:54 UTC).
        expected = timezone.localdate() + dt.timedelta(days=90)
        self.assertEqual(fu.next_retest_date, expected)

    def test_overdue_is_derived(self):
        ManagementResponse.objects.create(
            finding=self.f, response_text='x', action='y', owner='z',
            target_date=timezone.now().date() - dt.timedelta(days=5), agreed=True)
        fu = FollowUp.objects.create(finding=self.f, status=FollowUp.Status.IN_PROGRESS)
        self.assertTrue(fu.is_overdue)


class FraudFlagTests(TestCase):
    """The dashboard's 'Open fraud-flagged' tile (Module 8) must be reachable.

    Oprah's QA (2026-07-25): the tile could never leave 0 because the finding
    form had no fraud input at all — the flag was only settable indirectly, by
    choosing effect category 'Fraud exposure'. The form now has an explicit
    checkbox, so the API has to honour it on any effect category.
    """

    def setUp(self):
        self.auditor = User.objects.create_user('omogomotsi@alphadirect.co.bw',
                                                email='omogomotsi@alphadirect.co.bw', password='x')
        self.eng = Engagement.objects.create(reference='IA-2026-005', title='Fraud flag')
        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.auditor)

    def test_explicit_flag_on_non_fraud_category_persists(self):
        # e.g. a financial misstatement CAUSED by fraud — category financial,
        # flag on. Nothing may quietly reset it.
        payload = {**_api_finding_payload(self.eng.id),
                   'effect_category': 'financial', 'fraud_flag': True}
        r = self.client_api.post('/api/v1/internal-audit/findings/', payload, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(Finding.objects.get(id=r.data['id']).fraud_flag)

    def test_flag_defaults_off(self):
        r = self.client_api.post('/api/v1/internal-audit/findings/',
                                 _api_finding_payload(self.eng.id), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(Finding.objects.get(id=r.data['id']).fraud_flag)

    def test_flagged_finding_counts_on_dashboard_tile(self):
        payload = {**_api_finding_payload(self.eng.id),
                   'effect_category': 'financial', 'fraud_flag': True}
        self.assertEqual(
            self.client_api.post('/api/v1/internal-audit/findings/', payload, format='json').status_code,
            201)
        d = self.client_api.get('/api/v1/internal-audit/dashboard/')
        self.assertEqual(d.status_code, 200, d.content)
        self.assertEqual(d.data['tiles']['fraud_open']['value'], 1)


class IndependenceAccessTests(TestCase):
    def setUp(self):
        self.auditor = User.objects.create_user('omogomotsi@alphadirect.co.bw',
                                                 email='omogomotsi@alphadirect.co.bw', password='x')
        self.cfo = User.objects.create_user('pganesharajah@alphadirect.co.bw',
                                            email='pganesharajah@alphadirect.co.bw', password='x')
        self.staff = User.objects.create_user('random@alphadirect.co.bw',
                                              email='random@alphadirect.co.bw', password='x')
        self.super = User.objects.create_superuser('root', email='root@alphadirect.co.bw', password='x')
        self.eng = Engagement.objects.create(reference='IA-2026-004', title='Access test')

    def _post_finding(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c.post('/api/v1/internal-audit/findings/',
                      _api_finding_payload(self.eng.id), format='json')

    def test_auditor_can_create(self):
        r = self._post_finding(self.auditor)
        self.assertEqual(r.status_code, 201, r.content)

    def test_cfo_cannot_create_but_can_read(self):
        r = self._post_finding(self.cfo)
        self.assertEqual(r.status_code, 403)
        c = APIClient(); c.force_authenticate(user=self.cfo)
        self.assertEqual(c.get('/api/v1/internal-audit/dashboard/').status_code, 200)

    def test_superuser_cannot_create(self):
        # Independence: platform admin power does NOT grant edit of audit content.
        r = self._post_finding(self.super)
        self.assertEqual(r.status_code, 403)

    def test_random_staff_blocked_entirely(self):
        c = APIClient(); c.force_authenticate(user=self.staff)
        self.assertEqual(c.get('/api/v1/internal-audit/dashboard/').status_code, 403)

    def test_editor_matched_by_username_when_email_blank(self):
        # SSO may populate username (local-part) but leave .email blank — the
        # auditor must still be recognised as an editor.
        from internal_audit.access import is_editor
        u = User.objects.create_user('omogomotsi', email='', password='x')
        self.assertTrue(is_editor(u))


def _api_finding_payload(engagement_id):
    return {
        'engagement': str(engagement_id),
        'title': 'Test finding',
        'criteria': 'Policy X requires Y.',
        'condition': 'Y was not done.',
        'evidence_reference': 'WP-1',
        'root_cause': 'missing_control',
        'effect_category': 'operational',
        'effect_detail': 'Exposure Z.',
        'likelihood': 3,
        'impact': 3,
        'rating_justification': 'Because.',
    }
