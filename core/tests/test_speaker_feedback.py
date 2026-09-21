"""Speaker audience feedback (CFO 2026-08-03).

The point of the module is CONFIDENTIALITY: the public can submit, but only the
speaker can read. Checklist L6 (CFO-approved 2026-07-29) says any identity check
must be a POSITIVE match with a test proving unknown input is REFUSED — the trap
that burned the entity-code check (24-Jul) and the claims-are-ADIC check
(29-Jul), where an unrecognised value resolved to a real one and so satisfied the
control meant to stop it. These tests are that proof.
"""
from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from core import speaker_feedback_views as svf
from core.models import SpeakerFeedback

# The YPO survey in production was simplified to a two-field form (company +
# problem) on 2026-08-15. The full ten-pain / three-open-question path is still
# supported by the module — the next talk can opt back into it — so the tests
# keep a rated-path fixture around as `test-pain-grid` and exercise both surfaces.
SLUG = 'test-pain-grid'
FORM = f'/api/v1/speaker-feedback/form/{SLUG}/'
SUBMIT = f'/api/v1/speaker-feedback/form/{SLUG}/submit/'
RESPONSES = f'/api/v1/speaker-feedback/responses/{SLUG}/'
ACCESS = '/api/v1/speaker-feedback/access/'

YPO_SLUG = 'ypo-ai-roadmap-2026'      # the live simplified survey
YPO_FORM = f'/api/v1/speaker-feedback/form/{YPO_SLUG}/'
YPO_SUBMIT = f'/api/v1/speaker-feedback/form/{YPO_SLUG}/submit/'

# Install the rated-path fixture into the module's own SURVEYS dict so the URL
# resolver finds it — same shape the YPO survey used before the 2026-08-15
# simplification, so every existing rating/summary/confidentiality test keeps
# its coverage.
svf.SURVEYS[SLUG] = {
    'title': 'Rated-path fixture',
    'event': 'Test event',
    'session_date': '2099-01-01',
    'speaker': 'Test speaker',
    'intro': 'Test intro.',
    'confidentiality': 'Your answers go to the speaker only.',
    'scale_low': 'Not a problem for us',
    'scale_high': 'A huge problem for us',
    'pains': svf.YPO_PAINS,
    'open_questions': (
        {'key': 'biggest_question', 'required': True, 'label': 'q?'},
        {'key': 'wish_ai_did', 'required': False, 'label': 'w?'},
        {'key': 'tried_already', 'required': False, 'label': 't?'},
    ),
    'closes_on': '2099-12-31',
}

# The public endpoints are anon-throttled at 20/min; the tests below submit only
# a handful of rows, well inside that.


class SpeakerFeedbackPublicFormTest(APITestCase):
    def test_form_is_public_and_lists_ten_pains(self):
        r = APIClient().get(FORM)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body['pains']), 10)
        self.assertTrue(all(p['statement'] for p in body['pains']))
        # The confidentiality promise must reach the respondent — it is what
        # buys the honest answer.
        self.assertIn('speaker only', body['confidentiality'])

    def test_unknown_slug_is_refused_not_defaulted(self):
        """An unrecognised link must 404, never fall back to a real survey."""
        r = APIClient().get('/api/v1/speaker-feedback/form/not-a-real-survey/')
        self.assertEqual(r.status_code, 404)
        r2 = APIClient().post('/api/v1/speaker-feedback/form/not-a-real-survey/submit/',
                              {'biggest_question': 'x', 'pain_ratings': {'p1': 5}},
                              format='json')
        self.assertEqual(r2.status_code, 404)
        self.assertEqual(SpeakerFeedback.objects.count(), 0)

    def test_anonymous_submit_is_stored(self):
        r = APIClient().post(SUBMIT, {
            'pain_ratings': {'p1': 5, 'p4': 2},
            'biggest_question': 'Where do I start?',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        rec = SpeakerFeedback.objects.get()
        self.assertEqual(rec.pain_ratings, {'p1': 5, 'p4': 2})
        self.assertEqual(rec.respondent_name, '')      # anonymous is allowed
        self.assertFalse(rec.may_quote)                # default: do not quote

    def test_quote_consent_round_trips(self):
        """may_quote must persist BOTH ways. If a 'yes' silently stored as False
        the speaker would think nobody agreed to be quoted; if a missing value
        stored as True he would quote someone who never agreed."""
        c = APIClient()
        r = c.post(SUBMIT, {'pain_ratings': {'p1': 4}, 'biggest_question': 'q1',
                            'may_quote': True}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(SpeakerFeedback.objects.get(biggest_question='q1').may_quote)

        r = c.post(SUBMIT, {'pain_ratings': {'p1': 4}, 'biggest_question': 'q2',
                            'may_quote': False}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(SpeakerFeedback.objects.get(biggest_question='q2').may_quote)

        # Field absent entirely -> must default to "do NOT quote".
        r = c.post(SUBMIT, {'pain_ratings': {'p1': 4}, 'biggest_question': 'q3'},
                   format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(SpeakerFeedback.objects.get(biggest_question='q3').may_quote)

    def test_out_of_range_and_unknown_ratings_are_dropped(self):
        r = APIClient().post(SUBMIT, {
            'pain_ratings': {'p1': 9, 'p2': 0, 'p3': 'abc', 'p99': 5, 'p5': 3},
            'biggest_question': 'Cost?',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(SpeakerFeedback.objects.get().pain_ratings, {'p5': 3})

    def test_skipped_statement_is_not_scored_as_a_middle_value(self):
        """A skip must be absent, not coerced to 3 — that would drag the average
        toward the middle and hide the real pain."""
        r = APIClient().post(SUBMIT, {
            'pain_ratings': {'p1': 5, 'p2': None, 'p3': ''},
            'biggest_question': 'Which jobs go first?',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(SpeakerFeedback.objects.get().pain_ratings, {'p1': 5})

    def test_missing_required_answer_is_rejected(self):
        r = APIClient().post(SUBMIT, {'pain_ratings': {'p1': 4}}, format='json')
        self.assertEqual(r.status_code, 422)
        self.assertEqual(SpeakerFeedback.objects.count(), 0)

    def test_no_ratings_at_all_is_rejected(self):
        r = APIClient().post(SUBMIT, {'biggest_question': 'Anything'}, format='json')
        self.assertEqual(r.status_code, 422)
        self.assertEqual(SpeakerFeedback.objects.count(), 0)

    def test_invalid_headcount_band_is_discarded_not_stored(self):
        r = APIClient().post(SUBMIT, {
            'pain_ratings': {'p1': 4}, 'biggest_question': 'q',
            'headcount_band': '<script>alert(1)</script>',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(SpeakerFeedback.objects.get().headcount_band, '')


class SpeakerFeedbackConfidentialityTest(APITestCase):
    """Who may READ. This is the whole security story of the module."""

    def setUp(self):
        SpeakerFeedback.objects.create(
            survey_slug=SLUG, pain_ratings={'p1': 5},
            biggest_question='A confidential answer', company_name='Acme')

    def _get(self, user, url=RESPONSES):
        c = APIClient()
        if user is not None:
            c.force_authenticate(user=user)
        return c.get(url)

    def test_the_speaker_can_read(self):
        cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')
        r = self._get(cfo)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['response_count'], 1)
        self.assertEqual(len(body['summary']), 10)
        self.assertIn('confidential answer', body['responses'][0]['biggest_question'])

    def test_anonymous_cannot_read(self):
        self.assertIn(self._get(None).status_code, (401, 403))

    def test_ordinary_staff_cannot_read(self):
        staff = User.objects.create_user(
            'someone', email='someone@alphadirect.co.bw', password='x')
        self.assertEqual(self._get(staff).status_code, 403)

    def test_a_SUPERUSER_cannot_read(self):
        """'Confidential, my eyes' means the people who grant access still
        cannot read it. This is the deliberate difference from
        core.hris_access, where superusers ARE meant to see the data."""
        su = User.objects.create_superuser(
            'root', email='root@alphadirect.co.bw', password='x')
        self.assertEqual(self._get(su).status_code, 403)

    def test_a_lookalike_username_is_refused(self):
        """Positive EXACT match, never startswith — `pganesharajah2` is not him."""
        impostor = User.objects.create_user(
            'pganesharajah2', email='pganesharajah2@alphadirect.co.bw', password='x')
        self.assertEqual(self._get(impostor).status_code, 403)

    def test_the_right_local_part_on_a_FOREIGN_domain_is_refused(self):
        """Django emails are NOT unique. Without a domain check, anyone able to
        create a user could set email `pganesharajah@anything.com` and read the
        answers. Usernames ARE unique, so only the email leg needs the domain.
        (Fable 5 finding A1, PR #583.)"""
        for addr in ('pganesharajah@gmail.com', 'pganesharajah@alphadirect.co.bw.evil.com',
                     'pganesharajah'):
            with self.subTest(email=addr):
                u = User.objects.create_user(f'imposter-{abs(hash(addr))}', email=addr,
                                             password='x')
                self.assertEqual(self._get(u).status_code, 403, addr)

    def test_a_deactivated_reader_is_refused(self):
        cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')
        cfo.is_active = False
        cfo.save(update_fields=['is_active'])
        self.assertEqual(self._get(cfo).status_code, 403)

    def test_access_probe_matches_the_read_gate(self):
        cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')
        other = User.objects.create_user(
            'nobody', email='nobody@alphadirect.co.bw', password='x')
        self.assertTrue(self._get(cfo, ACCESS).json()['allowed'])
        self.assertFalse(self._get(other, ACCESS).json()['allowed'])

    @override_settings()
    def test_blank_env_override_does_not_open_the_module(self):
        """A blank OMNI_SPEAKER_FEEDBACK_READERS must fall back to the default,
        not be read as 'nobody is excluded'."""
        import os
        from core.speaker_feedback_views import _readers
        old = os.environ.get('OMNI_SPEAKER_FEEDBACK_READERS')
        try:
            os.environ['OMNI_SPEAKER_FEEDBACK_READERS'] = '   '
            self.assertEqual(_readers(), {'pganesharajah'})
        finally:
            if old is None:
                os.environ.pop('OMNI_SPEAKER_FEEDBACK_READERS', None)
            else:
                os.environ['OMNI_SPEAKER_FEEDBACK_READERS'] = old


class SpeakerFeedbackSummaryTest(APITestCase):
    def test_average_excludes_skips_and_ranks_worst_first(self):
        # p1: three people answer 5,5,4  -> avg 4.67, all top-box
        # p2: one person answers 1, two skip -> avg 1.0, computed on 1 answer
        for ratings in ({'p1': 5, 'p2': 1}, {'p1': 5}, {'p1': 4}):
            SpeakerFeedback.objects.create(
                survey_slug=SLUG, pain_ratings=ratings, biggest_question='q')
        cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')
        c = APIClient()
        c.force_authenticate(user=cfo)
        body = c.get(RESPONSES).json()

        by_key = {row['key']: row for row in body['summary']}
        self.assertEqual(by_key['p1']['answered'], 3)
        self.assertAlmostEqual(by_key['p1']['average'], 4.67, places=2)
        self.assertEqual(by_key['p1']['top_box'], 3)
        self.assertEqual(by_key['p1']['top_box_pct'], 100)
        # p2 had ONE answer of 1; the two skips must not appear as 3s.
        self.assertEqual(by_key['p2']['answered'], 1)
        self.assertEqual(by_key['p2']['average'], 1.0)
        # Worst pain first; statements nobody answered sink to the bottom.
        self.assertEqual(body['summary'][0]['key'], 'p1')
        self.assertIsNone(body['summary'][-1]['average'])

    def test_breadth_of_pain_outranks_a_single_high_score(self):
        """One person scoring 5 must NOT outrank four people scoring 4 — that
        would send the talk after an outlier instead of the room's real pain."""
        SpeakerFeedback.objects.create(
            survey_slug=SLUG, pain_ratings={'p8': 5}, biggest_question='outlier')
        for _ in range(4):
            SpeakerFeedback.objects.create(
                survey_slug=SLUG, pain_ratings={'p3': 4}, biggest_question='crowd')
        cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')
        c = APIClient()
        c.force_authenticate(user=cfo)
        summary = c.get(RESPONSES).json()['summary']
        self.assertEqual(summary[0]['key'], 'p3')   # 4 people at 4.0
        self.assertEqual(summary[1]['key'], 'p8')   # 1 person at 5.0


class YPOSimplifiedFormTest(APITestCase):
    """The live YPO survey was simplified 2026-08-15 to two fields: company name
    (required) and the problem the member wants AI to solve. These tests are what
    prove the rating path is genuinely optional per-survey (not dead code we
    forgot to remove), and that company_name is genuinely required on this one."""

    def test_form_has_no_rating_grid_and_one_open_question(self):
        r = APIClient().get(YPO_FORM)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['pains'], [])
        self.assertEqual(len(body['open_questions']), 1)
        self.assertFalse(body['require_company'])       # anonymous by default
        self.assertEqual(body['closes_on'], '2026-08-22')

    def test_company_and_problem_submit_is_accepted(self):
        r = APIClient().post(YPO_SUBMIT, {
            'company_name': 'Acme Ltd',
            'biggest_question': 'How do I stop paying vendors for spreadsheets?',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        rec = SpeakerFeedback.objects.get(survey_slug=YPO_SLUG)
        self.assertEqual(rec.company_name, 'Acme Ltd')
        self.assertEqual(rec.pain_ratings, {})       # no grid, no scores stored

    def test_anonymous_submit_is_accepted(self):
        """Company + name are both optional — a member who wants to answer
        anonymously must go through, not be blocked."""
        r = APIClient().post(YPO_SUBMIT, {
            'biggest_question': 'How do I start?',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        rec = SpeakerFeedback.objects.get(survey_slug=YPO_SLUG)
        self.assertEqual(rec.company_name, '')
        self.assertEqual(rec.respondent_name, '')

    def test_missing_problem_is_rejected(self):
        r = APIClient().post(YPO_SUBMIT, {
            'company_name': 'Acme Ltd',
        }, format='json')
        self.assertEqual(r.status_code, 422)
        self.assertEqual(SpeakerFeedback.objects.filter(survey_slug=YPO_SLUG).count(), 0)

    def test_pain_ratings_are_not_required_and_are_dropped_if_sent(self):
        """A caller posting stale rating data at the simplified form must not
        be rejected — and the stale keys must not be stored (there's no grid
        for them to match against)."""
        r = APIClient().post(YPO_SUBMIT, {
            'company_name': 'Acme Ltd',
            'biggest_question': 'What do I automate first?',
            'pain_ratings': {'p1': 5, 'p2': 3},
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            SpeakerFeedback.objects.get(survey_slug=YPO_SLUG).pain_ratings, {})
