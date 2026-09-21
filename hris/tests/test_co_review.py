"""Tests for the joint 50/50 monthly rating (CFO 2026-07-26).

Origin: Kago Tshutlhedi tested the Monthly Manager Return and asked why the senior
accountants were not on his team. They report to the CFO. The CFO's answer is that
both of them rate those people and the score splits 50/50 — so the Finance Manager
has a say, and the CFO keeps a live read on the bench.

Guards worth having: only the two named raters can score, nobody scores themselves,
the second rater cannot see the first score until their own is in (no anchoring),
the combined figure is computed not stored, and one rater alone is never presented
as a finished score.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company
from hris.co_review_models import CoRating, RaterRole, combined_score
from hris.manager_return_service import co_review_profiles, team_profiles
from hris.models import HRISProfile
from payroll.models import Employee


@override_settings(ELRA_PERF_ENABLED=True)
class CoReviewTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CR', name='Co-Review Co.')

        cls.cfo_user = User.objects.create_user('pg', email='pganesharajah@alphadirect.co.bw')
        cls.fm_user = User.objects.create_user('kt', email='ktshutlhedi@alphadirect.co.bw')
        cls.other_user = User.objects.create_user('rando', email='rando@alphadirect.co.bw')
        cls.subject_user = User.objects.create_user('sa', email='sa@alphadirect.co.bw')

        cls.cfo = Employee.objects.create(
            employee_number='C1', full_name='The CFO', company=cls.co,
            email='pganesharajah@alphadirect.co.bw', user=cls.cfo_user,
            department='C-Suite', job_title='Chief Financial Officer')
        cls.fm = Employee.objects.create(
            employee_number='F1', full_name='Finance Manager', company=cls.co,
            email='ktshutlhedi@alphadirect.co.bw', user=cls.fm_user,
            department='Finance', job_title='Manager - Finance & Planning')
        cls.other = Employee.objects.create(
            employee_number='O1', full_name='Unrelated Person', company=cls.co,
            email='rando@alphadirect.co.bw', user=cls.other_user)
        # The senior accountant: line-manages to the CFO, co-reviewed by the FM.
        cls.sa = Employee.objects.create(
            employee_number='S1', full_name='Senior Accountant', company=cls.co,
            email='sa@alphadirect.co.bw', user=cls.subject_user,
            department='Finance', job_title='Senior Accountant - Finance & Planning')

        HRISProfile.objects.create(employee=cls.fm, manager=cls.cfo)
        cls.sa_profile = HRISProfile.objects.create(
            employee=cls.sa, manager=cls.cfo, co_manager=cls.fm, co_manager_weight=50)

    # ── the roster question Kago actually asked ─────────────────────────────
    def test_co_reviewed_person_is_not_in_the_line_roster(self):
        """The senior accountant must NOT appear as the FM's direct report — the
        org chart does not change, only who gives feedback."""
        self.assertNotIn(self.sa_profile,
                         list(team_profiles(self.fm)))
        self.assertIn(self.sa_profile, list(co_review_profiles(self.fm)))

    def test_line_manager_does_not_see_them_twice(self):
        """The CFO line-manages AND could co-review; they must not be listed twice."""
        self.sa_profile.co_manager = self.cfo
        self.sa_profile.save(update_fields=['co_manager'])
        self.assertEqual(list(co_review_profiles(self.cfo)), [])

    def test_they_show_on_the_monthly_return_payload(self):
        self.client.force_authenticate(self.fm_user)
        r = self.client.get(reverse('hris:api-manager-return'), {'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 200, r.content)
        names = [x['name'] for x in r.json().get('co_reviewed', [])]
        self.assertIn('Senior Accountant', names)

    def test_a_co_reviewer_with_no_line_reports_still_reaches_the_page(self):
        """Otherwise their 50% could never be entered."""
        lonely_user = User.objects.create_user('lonely', email='lonely@alphadirect.co.bw')
        lonely = Employee.objects.create(
            employee_number='L1', full_name='No Team Reviewer', company=self.co,
            email='lonely@alphadirect.co.bw', user=lonely_user)
        self.sa_profile.co_manager = lonely
        self.sa_profile.save(update_fields=['co_manager'])
        self.client.force_authenticate(lonely_user)
        r = self.client.get(reverse('hris:api-manager-return'))
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body.get('co_review_only'))
        self.assertEqual([x['name'] for x in body['co_reviewed']], ['Senior Accountant'])

    # ── who may score ───────────────────────────────────────────────────────
    def _rate(self, user, score, comment='ok', **over):
        self.client.force_authenticate(user)
        body = {'profile_id': str(self.sa_profile.id), 'score': score,
                'comment': comment, 'year': 2026, 'month': 6}
        body.update(over)
        return self.client.post(reverse('hris:api-co-review-rate'), body, format='json')

    def test_line_manager_and_co_reviewer_may_score(self):
        self.assertEqual(self._rate(self.cfo_user, 80).status_code, 200)
        self.assertEqual(self._rate(self.fm_user, 60).status_code, 200)

    def test_a_stranger_cannot_score(self):
        r = self._rate(self.other_user, 100)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(CoRating.objects.count(), 0)

    def test_nobody_scores_themselves(self):
        """Even if the data made someone their own manager."""
        self.sa_profile.co_manager = self.sa
        self.sa_profile.save(update_fields=['co_manager'])
        r = self._rate(self.subject_user, 100)
        self.assertEqual(r.status_code, 403)

    def test_score_must_be_0_to_100(self):
        for bad in (-1, 101, 'abc', None):
            r = self._rate(self.cfo_user, bad)
            self.assertIn(r.status_code, (400, 403), f'{bad} was accepted')
        self.assertEqual(CoRating.objects.count(), 0)

    def test_a_rater_may_revise_their_own_score_only_once_per_month(self):
        self._rate(self.cfo_user, 70)
        self._rate(self.cfo_user, 75)
        rows = CoRating.objects.filter(profile=self.sa_profile, rater=self.cfo_user)
        self.assertEqual(rows.count(), 1)          # updated, not duplicated
        self.assertEqual(rows.first().score, 75)

    def test_a_locked_rating_cannot_be_changed(self):
        self._rate(self.cfo_user, 70)
        CoRating.objects.filter(rater=self.cfo_user).update(is_locked=True)
        r = self._rate(self.cfo_user, 99)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(CoRating.objects.get(rater=self.cfo_user).score, 70)

    # ── the 50/50 arithmetic ────────────────────────────────────────────────
    def test_fifty_fifty_blend(self):
        self._rate(self.cfo_user, 80)
        self._rate(self.fm_user, 60)
        blend = combined_score(self.sa_profile, 2026, 6)
        self.assertEqual(blend['combined'], 70.0)
        self.assertTrue(blend['complete'])
        self.assertEqual(blend['spread'], 20)
        self.assertTrue(blend['disputed'])          # 20+ apart = worth a conversation

    def test_a_different_weight_is_honoured(self):
        self.sa_profile.co_manager_weight = 25
        self.sa_profile.save(update_fields=['co_manager_weight'])
        self._rate(self.cfo_user, 80)               # line gets 75%
        self._rate(self.fm_user, 40)                # co gets 25%
        self.assertEqual(combined_score(self.sa_profile, 2026, 6)['combined'], 70.0)

    def test_one_rater_alone_is_not_presented_as_final(self):
        """The dangerous case: half a score quietly shown as the answer."""
        self._rate(self.cfo_user, 90)
        blend = combined_score(self.sa_profile, 2026, 6)
        self.assertFalse(blend['complete'])
        self.assertEqual(blend['waiting_on'], ['co-reviewer'])
        self.assertIsNone(blend['spread'])

    def test_no_ratings_at_all_waits_on_both(self):
        blend = combined_score(self.sa_profile, 2026, 6)
        self.assertIsNone(blend['combined'])
        self.assertEqual(sorted(blend['waiting_on']), ['co-reviewer', 'line manager'])

    def test_close_scores_are_not_flagged_as_disputed(self):
        self._rate(self.cfo_user, 72)
        self._rate(self.fm_user, 68)
        blend = combined_score(self.sa_profile, 2026, 6)
        self.assertEqual(blend['combined'], 70.0)
        self.assertFalse(blend['disputed'])

    def test_the_combined_score_is_computed_not_stored(self):
        """There is no editable 'combined' column that could drift from its parts."""
        self.assertFalse(any(f.name == 'combined' for f in CoRating._meta.get_fields()))

    # ── anchoring guard ─────────────────────────────────────────────────────
    def test_second_rater_cannot_see_the_first_score_before_entering_their_own(self):
        self._rate(self.cfo_user, 95)
        self.client.force_authenticate(self.fm_user)
        r = self.client.get(reverse('hris:api-co-review'), {'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 200, r.content)
        row = [x for x in r.json()['shared'] if x['name'] == 'Senior Accountant'][0]
        self.assertIsNone(row['line_score'])        # hidden
        self.assertIsNone(row['combined'])

    def test_once_both_are_in_each_sees_the_other(self):
        self._rate(self.cfo_user, 95)
        self._rate(self.fm_user, 55)
        self.client.force_authenticate(self.fm_user)
        r = self.client.get(reverse('hris:api-co-review'), {'year': 2026, 'month': 6})
        row = [x for x in r.json()['shared'] if x['name'] == 'Senior Accountant'][0]
        self.assertEqual(row['line_score'], 95)
        self.assertEqual(row['co_score'], 55)
        self.assertEqual(row['combined'], 75.0)

    # ── the subject's own view ──────────────────────────────────────────────
    def test_the_person_sees_the_blend_but_not_who_scored_what(self):
        self._rate(self.cfo_user, 80)
        self._rate(self.fm_user, 60)
        self.client.force_authenticate(self.subject_user)
        r = self.client.get(reverse('hris:api-co-review-blend'),
                            {'employee': str(self.sa.id), 'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['combined'], 70.0)
        self.assertIsNone(body['line_score'])
        self.assertIsNone(body['co_score'])

    def test_a_stranger_cannot_read_someone_elses_blend(self):
        self.client.force_authenticate(self.other_user)
        r = self.client.get(reverse('hris:api-co-review-blend'),
                            {'employee': str(self.sa.id), 'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 403)

    def test_both_raters_can_read_the_blend(self):
        self._rate(self.cfo_user, 80)
        self._rate(self.fm_user, 60)
        for u in (self.cfo_user, self.fm_user):
            self.client.force_authenticate(u)
            r = self.client.get(reverse('hris:api-co-review-blend'),
                                {'employee': str(self.sa.id), 'year': 2026, 'month': 6})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()['combined'], 70.0)

    # ── model validation ────────────────────────────────────────────────────
    def test_model_rejects_a_silly_period(self):
        for y, m in ((1999, 6), (2026, 13), (2026, 0)):
            obj = CoRating(profile=self.sa_profile, period_year=y, period_month=m,
                           rater=self.cfo_user, rater_role=RaterRole.LINE, score=50)
            with self.assertRaises(ValidationError):
                obj.clean()

    def test_terminated_people_drop_out_of_the_co_review_list(self):
        self.sa.status = Employee.Status.TERMINATED
        self.sa.save(update_fields=['status'])
        self.assertEqual(list(co_review_profiles(self.fm)), [])


@override_settings(ELRA_PERF_ENABLED=True)
class AdditionalReviewerTest(APITestCase):
    """Operations reviewers (CFO 2026-08-12): a third person may review + feedback
    someone who line-reports elsewhere, WITHOUT joining the line/co blend."""

    @classmethod
    def setUpTestData(cls):
        from hris.co_review_models import AdditionalReviewer
        cls.co = Company.objects.create(code='OPS', name='Ops Co.')

        cls.ithead_user = User.objects.create_user('ith', email='ith@alphadirect.co.bw')
        cls.cov_user = User.objects.create_user('cov', email='cov@alphadirect.co.bw')
        cls.bharath_user = User.objects.create_user('bh', email='bh@alphadirect.co.bw')
        cls.subj_user = User.objects.create_user('it1', email='it1@alphadirect.co.bw')
        cls.stranger_user = User.objects.create_user('strngr', email='strngr@alphadirect.co.bw')

        mk = lambda n, fn, u, **k: Employee.objects.create(
            employee_number=n, full_name=fn, company=cls.co, email=u.email, user=u, **k)
        cls.ithead = mk('I1', 'IT Head', cls.ithead_user, department='IT')
        cls.cov = mk('V1', 'Co Reviewer', cls.cov_user, department='IT')
        cls.bharath = mk('B1', 'Bharath Ops', cls.bharath_user, department='Operations')
        cls.subj = mk('T1', 'IT Person', cls.subj_user, department='IT', job_title='IT Officer')
        cls.stranger = mk('X1', 'Stranger', cls.stranger_user)

        for e in (cls.ithead, cls.cov, cls.bharath, cls.stranger):
            HRISProfile.objects.create(employee=e)
        # Subject line-reports to IT Head, co-reviewed by Co Reviewer 50/50.
        cls.subj_profile = HRISProfile.objects.create(
            employee=cls.subj, manager=cls.ithead, co_manager=cls.cov, co_manager_weight=50)
        # Bharath is an operations reviewer on top — outside the blend.
        AdditionalReviewer.objects.create(
            profile=cls.subj_profile, reviewer=cls.bharath, reason='Operations oversight')

    def _rate(self, user, score, comment=''):
        self.client.force_authenticate(user)
        return self.client.post(reverse('hris:api-co-review-rate'),
                                {'profile_id': str(self.subj_profile.id),
                                 'score': score, 'comment': comment,
                                 'year': 2026, 'month': 6}, format='json')

    def test_additional_reviewer_can_rate(self):
        r = self._rate(self.bharath_user, 42, 'ops view')
        self.assertEqual(r.status_code, 200, r.content)
        row = CoRating.objects.get(profile=self.subj_profile, rater=self.bharath_user)
        self.assertEqual(row.rater_role, RaterRole.ADDITIONAL)
        self.assertEqual(row.score, 42)

    def test_additional_rating_does_NOT_move_the_line_co_blend(self):
        """The whole point: Bharath's score is recorded but never dilutes the
        existing line/co split (protects e.g. the CFO/Kago 50/50)."""
        self._rate(self.ithead_user, 80)          # line
        self._rate(self.cov_user, 60)             # co  -> 50/50 blend = 70
        before = combined_score(self.subj_profile, 2026, 6)
        self.assertEqual(before['combined'], 70.0)
        self.assertTrue(before['complete'])

        self._rate(self.bharath_user, 0)          # additional, wildly different
        after = combined_score(self.subj_profile, 2026, 6)
        self.assertEqual(after['combined'], 70.0)          # UNCHANGED
        self.assertEqual(after['line_score'], 80)
        self.assertEqual(after['co_score'], 60)
        self.assertTrue(after['complete'])
        # ...but his rating IS recorded, separately.
        self.assertEqual(len(after['additional']), 1)
        self.assertEqual(after['additional'][0]['score'], 0)
        self.assertEqual(after['additional'][0]['rater_id'], self.bharath_user.id)

    def test_stranger_still_cannot_rate(self):
        r = self._rate(self.stranger_user, 99)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertFalse(CoRating.objects.filter(
            profile=self.subj_profile, rater=self.stranger_user).exists())

    def test_shows_in_my_ratings_additional_bucket(self):
        self.client.force_authenticate(self.bharath_user)
        r = self.client.get(reverse('hris:api-co-review'), {'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 200, r.content)
        names = [x['name'] for x in r.data.get('additional', [])]
        self.assertIn('IT Person', names)
        self.assertEqual(r.data.get('additional_count'), 1)

    def test_shows_on_monthly_return_even_with_no_line_team(self):
        """Bharath has no line reports in this fixture — he must still reach the
        page to review the people assigned to him."""
        self.client.force_authenticate(self.bharath_user)
        r = self.client.get(reverse('hris:api-manager-return'), {'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 200, r.content)
        names = [x['name'] for x in (r.data.get('additional_reviewed') or [])]
        self.assertIn('IT Person', names)

    def test_helpers_keep_them_out_of_line_and_co(self):
        from hris.manager_return_service import additional_review_profiles
        self.assertIn(self.subj_profile, list(additional_review_profiles(self.bharath)))
        self.assertEqual(list(team_profiles(self.bharath)), [])
        self.assertEqual(list(co_review_profiles(self.bharath)), [])

    def test_cannot_be_own_additional_reviewer(self):
        from hris.co_review_models import AdditionalReviewer
        with self.assertRaises(ValidationError):
            AdditionalReviewer(profile=self.subj_profile, reviewer=self.subj).clean()

    def test_assign_command_idempotent_and_skips_own_reports(self):
        from django.core.management import call_command
        from io import StringIO
        from hris.co_review_models import AdditionalReviewer
        # Assign Bharath to the IT Head (whom he does NOT manage) — fresh row.
        HRISProfile.objects.filter(employee=self.ithead).update(manager=None)
        out = StringIO()
        call_command('assign_additional_reviewer', reviewer='bh@alphadirect.co.bw',
                     emails='ith@alphadirect.co.bw', commit=True, stdout=out)
        self.assertTrue(AdditionalReviewer.objects.filter(
            profile__employee=self.ithead, reviewer=self.bharath).exists())
        # Re-run: idempotent, no duplicate.
        call_command('assign_additional_reviewer', reviewer='bh@alphadirect.co.bw',
                     emails='ith@alphadirect.co.bw', commit=True, stdout=StringIO())
        self.assertEqual(AdditionalReviewer.objects.filter(
            profile__employee=self.ithead, reviewer=self.bharath).count(), 1)
        # Skips a person Bharath already line-manages — no NEW assignment made.
        # (Use cov, who has no pre-existing additional-reviewer row.)
        HRISProfile.objects.filter(employee=self.cov).update(manager=self.bharath)
        call_command('assign_additional_reviewer', reviewer='bh@alphadirect.co.bw',
                     emails='cov@alphadirect.co.bw', commit=True, stdout=StringIO())
        self.assertFalse(AdditionalReviewer.objects.filter(
            profile__employee=self.cov, reviewer=self.bharath).exists())

    def test_assign_by_department(self):
        """The --department path (matches Employee.department OR job_title)."""
        from django.core.management import call_command
        from io import StringIO
        from hris.co_review_models import AdditionalReviewer
        call_command('assign_additional_reviewer', reviewer='bh@alphadirect.co.bw',
                     department='IT', commit=True, stdout=StringIO())
        # IT Head is department=IT and Bharath does not manage them -> assigned.
        self.assertTrue(AdditionalReviewer.objects.filter(
            profile__employee=self.ithead, reviewer=self.bharath).exists())

    def test_subject_cannot_see_additional_reviewer_scores(self):
        """The subject reads their blend but NEVER an operations reviewer's raw
        score/comment (which carries rater_id). Regression for the blend leak."""
        self._rate(self.bharath_user, 33, 'candid ops note')   # Bharath rates the subject
        self.client.force_authenticate(self.subj_user)          # the subject themselves
        r = self.client.get(reverse('hris:api-co-review-blend'),
                            {'employee': str(self.subj.id), 'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data.get('additional'), [])          # scrubbed, not Bharath's 33

    def test_additional_profile_drops_off_once_line_or_co(self):
        """If the reviewer later line/co-manages the person, that relationship wins
        and they are not also listed as an additional review (no double-count)."""
        from hris.manager_return_service import additional_review_profiles
        self.subj_profile.co_manager = self.bharath
        self.subj_profile.save(update_fields=['co_manager'])
        self.assertNotIn(self.subj_profile, list(additional_review_profiles(self.bharath)))
