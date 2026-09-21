"""OKR self-service — Unami's request, 30 Jul 2026.

Her OKR training deck defines Objective → Key Result → Initiative, and steps 4-6
put the employee in the loop: contributors share their own OKRs, track progress
through the quarter, and score at the end. These tests pin the parts that must not
break:

  * an employee sees company + their own department + their own objectives, and
    NEVER a colleague's individual goals,
  * they can add their own sub-objective ("how will they achieve"),
  * they can check in on their own key result but not on somebody else's,
  * self-reported progress never touches score_h1 / score_h2 (the appraisal scores
    the 9-box grid reads),
  * attainment maths works for "increase to", "reduce to" and "done".
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from hris.models import HRISProfile, OKR
from hris.okr_models import KeyResult, OKRCheckIn
from payroll.models import Employee

PERIOD = 'FY27'


class AttainmentMathsTests(TestCase):
    """Pure maths — the deck's "Increase __ from X to Y" / "Reduce __ by X%"."""

    def _kr(self, **kw):
        return KeyResult(**kw)

    def test_increase_to_target(self):
        kr = self._kr(direction='increase', start_value=Decimal('10'),
                      target_value=Decimal('20'), current_value=Decimal('15'))
        self.assertEqual(kr.attainment_pct, 50.0)

    def test_reduce_to_target(self):
        # Reduce claims turnaround from 12 days to 9; at 10.5 we are half way.
        kr = self._kr(direction='decrease', start_value=Decimal('12'),
                      target_value=Decimal('9'), current_value=Decimal('10.5'))
        self.assertEqual(kr.attainment_pct, 50.0)

    def test_overshoot_and_undershoot_are_clamped(self):
        over = self._kr(direction='increase', start_value=Decimal('0'),
                        target_value=Decimal('10'), current_value=Decimal('25'))
        under = self._kr(direction='increase', start_value=Decimal('0'),
                         target_value=Decimal('10'), current_value=Decimal('-5'))
        self.assertEqual(over.attainment_pct, 100.0)
        self.assertEqual(under.attainment_pct, 0.0)

    def test_not_reported_is_none_not_zero(self):
        """"Nobody has reported" and "reported as zero" must stay different."""
        kr = self._kr(direction='increase', start_value=Decimal('0'),
                      target_value=Decimal('10'), current_value=None)
        self.assertIsNone(kr.attainment_pct)

    def test_done_type_is_binary(self):
        self.assertEqual(self._kr(direction='done', is_done=True).attainment_pct, 100.0)
        self.assertIsNone(self._kr(direction='done', is_done=False).attainment_pct)


class SelfServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(full_name='Team Member', department='Claims',
                                          email='member@alphadirect.co.bw',
                                          employee_number='OKR-1')
        cls.other = Employee.objects.create(full_name='Someone Else', department='Finance',
                                            email='other@alphadirect.co.bw',
                                            employee_number='OKR-2')
        cls.prof = HRISProfile.objects.create(employee=cls.emp)
        cls.other_prof = HRISProfile.objects.create(employee=cls.other)
        cls.user = User.objects.create_user('member', email='member@alphadirect.co.bw',
                                            password='x')
        cls.other_user = User.objects.create_user('other', email='other@alphadirect.co.bw',
                                                  password='x')
        cls.company_obj = OKR.objects.create(scope='company', period=PERIOD,
                                            name='Grow the book', weight_pct=Decimal('0'))
        cls.dept_obj = OKR.objects.create(scope='department', period=PERIOD,
                                          name='Claims turnaround', target='Claims')
        cls.mine = OKR.objects.create(scope='individual', period=PERIOD, profile=cls.prof,
                                      name='My objective', weight_pct=Decimal('100'))
        cls.theirs = OKR.objects.create(scope='individual', period=PERIOD,
                                        profile=cls.other_prof, name='Their private goal')

    def setUp(self):
        self.client.force_login(self.user)

    def test_employee_sees_company_department_and_own_but_not_a_colleagues(self):
        r = self.client.get(reverse('v1-hris-okr-mine'))
        self.assertEqual(r.status_code, 200, r.content[:400])
        names = [o['name'] for o in r.json()['objectives']]
        self.assertIn('Grow the book', names)
        self.assertIn('Claims turnaround', names)
        self.assertIn('My objective', names)
        self.assertNotIn('Their private goal', names)     # never a colleague's

    def test_company_first_then_department_then_individual(self):
        r = self.client.get(reverse('v1-hris-okr-mine'))
        scopes = [o['scope'] for o in r.json()['objectives']]
        self.assertEqual(scopes, sorted(scopes, key=lambda s: {'company': 0, 'department': 1,
                                                               'individual': 2}[s]))

    def test_add_my_sub_objective_under_the_department_goal(self):
        r = self.client.post(reverse('v1-hris-okr-mine-sub'), {
            'objective_id': str(self.dept_obj.pk),
            'name': 'Chase 10 open claims a day',
            'kind': 'initiative', 'direction': 'increase',
            'start_value': '0', 'target_value': '10', 'unit': 'claims',
        }, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content[:400])
        kr = KeyResult.objects.get(name='Chase 10 open claims a day')
        self.assertEqual(kr.owner_id, self.prof.pk)        # always owned by the caller
        self.assertEqual(kr.objective_id, self.dept_obj.pk)
        self.assertTrue(r.json()['key_result']['is_mine'])

    def test_cannot_attach_a_sub_objective_to_a_colleagues_objective(self):
        r = self.client.post(reverse('v1-hris-okr-mine-sub'), {
            'objective_id': str(self.theirs.pk), 'name': 'Sneaky',
        }, content_type='application/json')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(KeyResult.objects.filter(name='Sneaky').exists())

    def test_check_in_records_history_and_moves_progress(self):
        kr = KeyResult.objects.create(objective=self.mine, name='Cut backlog',
                                      direction='decrease', start_value=Decimal('100'),
                                      target_value=Decimal('50'), owner=self.prof)
        r = self.client.post(reverse('v1-hris-okr-mine-checkin'), {
            'key_result_id': str(kr.pk), 'value': '75', 'confidence': 'at_risk',
            'note': 'Waiting on assessors.',
        }, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content[:400])
        kr.refresh_from_db()
        self.assertEqual(kr.current_value, Decimal('75.00'))
        self.assertEqual(kr.attainment_pct, 50.0)
        self.assertEqual(OKRCheckIn.objects.filter(key_result=kr).count(), 1)
        self.assertEqual(r.json()['key_result']['attainment_pct'], 50.0)

    def test_cannot_check_in_on_someone_elses_key_result(self):
        kr = KeyResult.objects.create(objective=self.theirs, name='Theirs',
                                      owner=self.other_prof)
        r = self.client.post(reverse('v1-hris-okr-mine-checkin'), {
            'key_result_id': str(kr.pk), 'value': '99',
        }, content_type='application/json')
        self.assertEqual(r.status_code, 403)
        kr.refresh_from_db()
        self.assertIsNone(kr.current_value)

    def test_self_reported_progress_never_touches_the_appraisal_scores(self):
        """score_h1/h2 feed the 9-box grid. A self-reported check-in must not move
        them, or an employee could rate themselves."""
        self.mine.score_h1 = Decimal('3.00')
        self.mine.save(update_fields=['score_h1'])
        kr = KeyResult.objects.create(objective=self.mine, name='X', direction='increase',
                                      start_value=Decimal('0'), target_value=Decimal('10'),
                                      owner=self.prof)
        self.client.post(reverse('v1-hris-okr-mine-checkin'),
                         {'key_result_id': str(kr.pk), 'value': '10'},
                         content_type='application/json')
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.score_h1, Decimal('3.00'))
        self.assertIsNone(self.mine.score_h2)

    def test_objective_progress_rolls_up_from_key_results_only(self):
        KeyResult.objects.create(objective=self.mine, name='A', direction='increase',
                                 start_value=Decimal('0'), target_value=Decimal('10'),
                                 current_value=Decimal('10'), owner=self.prof)
        KeyResult.objects.create(objective=self.mine, name='B', direction='increase',
                                 start_value=Decimal('0'), target_value=Decimal('10'),
                                 current_value=Decimal('0'), owner=self.prof)
        r = self.client.get(reverse('v1-hris-okr-mine'))
        mine = next(o for o in r.json()['objectives'] if o['name'] == 'My objective')
        self.assertEqual(mine['progress_pct'], 50.0)           # (100 + 0) / 2
        self.assertEqual(len(mine['key_results']), 2)

    def test_initiatives_and_key_results_are_reported_separately(self):
        KeyResult.objects.create(objective=self.mine, name='KR', kind='key_result',
                                 owner=self.prof)
        KeyResult.objects.create(objective=self.mine, name='Init', kind='initiative',
                                 owner=self.prof)
        r = self.client.get(reverse('v1-hris-okr-mine'))
        mine = next(o for o in r.json()['objectives'] if o['name'] == 'My objective')
        self.assertEqual([k['name'] for k in mine['key_results']], ['KR'])
        self.assertEqual([k['name'] for k in mine['initiatives']], ['Init'])

    def test_history_is_readable_for_a_visible_objective(self):
        kr = KeyResult.objects.create(objective=self.dept_obj, name='Dept KR',
                                      owner=self.prof)
        OKRCheckIn.objects.create(key_result=kr, author=self.prof, value=Decimal('1'),
                                  note='first')
        r = self.client.get(reverse('v1-hris-okr-mine-history', args=[str(kr.pk)]))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(len(r.json()['check_ins']), 1)
        self.assertEqual(r.json()['check_ins'][0]['note'], 'first')

    def test_history_refused_for_a_colleagues_objective(self):
        kr = KeyResult.objects.create(objective=self.theirs, name='Theirs', owner=self.other_prof)
        r = self.client.get(reverse('v1-hris-okr-mine-history', args=[str(kr.pk)]))
        self.assertEqual(r.status_code, 403)

    def test_no_employee_record_is_handled_not_crashed(self):
        stranger = User.objects.create_user('stranger', email='stranger@x.com', password='x')
        self.client.force_login(stranger)
        r = self.client.get(reverse('v1-hris-okr-mine'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['objectives'], [])
