"""Tests for the Induction & Training Academy.

The anti-cheat properties are the reason this module exists, so most of these
assert a property that would be FALSE if the corresponding guard were removed:

  * two people sitting side by side get different papers  -> round-robin
  * no two papers share too many questions                -> overlap cap
  * the same paper looks different to two candidates      -> per-sitting shuffles
  * "the answer to question 3 is B" does not travel       -> both shuffles
  * the exam will not open until the slides were read     -> dwell gate
  * a made-up rule cannot get into the bank               -> source-quote check
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from hris.training_ai import validate_question, validate_slide, _norm  # noqa: F401
from hris.training_models import (TrainingAttempt, TrainingCourse,
                                  TrainingQuestion)
from hris.training_service import (MAX_PAPER_OVERLAP, NotEnoughQuestions,
                                   build_versions, exam_unlocked, issue_attempt,
                                   paper_for, score_attempt)

User = get_user_model()


def make_course(*, bank=120, per=30, versions=15, **kw) -> TrainingCourse:
    course = TrainingCourse.objects.create(
        title='Test course', slug=kw.pop('slug', 'test-course'),
        questions_per_paper=per, version_count=versions,
        status=TrainingCourse.Status.PUBLISHED, **kw)
    for i in range(bank):
        TrainingQuestion.objects.create(
            course=course, stem=f'Question {i}?',
            options=[f'q{i} right', f'q{i} wrong a', f'q{i} wrong b', f'q{i} wrong c'],
            correct_index=0, topic='General', section_ref='7.1')
    return course


class BuildPapersTests(TestCase):
    def test_builds_the_requested_number_of_papers_each_the_right_length(self):
        course = make_course()
        versions = build_versions(course, seed=1)
        self.assertEqual(len(versions), 15)
        for v in versions:
            self.assertEqual(len(v.question_ids), 30)
            self.assertEqual(len(set(v.question_ids)), 30,
                             'a paper must not repeat a question')

    def test_papers_overlap_less_than_unconstrained_random_would(self):
        """The overlap search has to be doing real work.

        MAX_PAPER_OVERLAP is a target the draw searches towards, not a promise
        it can always keep: with 15 papers of 30 drawn from a 60-question bank,
        an average pair must share 15 and no arrangement gets every pair under
        12. So asserting a hard ceiling either fails (tight bank) or passes
        whether or not the cap exists (roomy bank) — it certifies nothing either
        way.

        What IS a real property is that the draw beats plain random sampling.
        This measures both on the same bank with the same seed and requires the
        builder to win. Remove the overlap search from `_draw_distinct` and this
        goes red.
        """
        course = make_course(bank=60)
        versions = build_versions(course, seed=7)

        def worst_pair(sets):
            worst = 0
            for i in range(len(sets)):
                for j in range(i + 1, len(sets)):
                    worst = max(worst, len(sets[i] & sets[j]))
            return worst

        built = worst_pair([set(v.question_ids) for v in versions])

        # Control: the same 15 papers drawn with no regard for overlap at all.
        import random as _r
        bank_ids = [str(q.id) for q in course.questions.all()]
        rng = _r.Random(7)
        control = worst_pair([set(rng.sample(bank_ids, 30)) for _ in range(15)])

        self.assertLess(built, control,
                        f'the overlap search gained nothing: builder worst={built}, '
                        f'plain random worst={control}')

    def test_with_a_roomy_bank_overlap_lands_under_the_target(self):
        """At the real induction bank size the target is comfortably met."""
        course = make_course(bank=180)
        versions = build_versions(course, seed=11)
        sets = [set(v.question_ids) for v in versions]
        worst = max(len(sets[i] & sets[j])
                    for i in range(len(sets)) for j in range(i + 1, len(sets)))
        self.assertLessEqual(worst, MAX_PAPER_OVERLAP,
                             f'two papers share {worst} questions')

    def test_refuses_to_build_when_the_bank_is_smaller_than_one_paper(self):
        course = make_course(bank=10, slug='tiny')
        with self.assertRaises(NotEnoughQuestions):
            build_versions(course)

    def test_rebuild_with_the_same_seed_is_deterministic(self):
        course = make_course()
        a = [v.question_ids for v in build_versions(course, seed=42)]
        b = [v.question_ids for v in build_versions(course, seed=42)]
        self.assertEqual(a, b)


class PaperIssueTests(TestCase):
    def setUp(self):
        self.course = make_course()
        build_versions(self.course, seed=3)

    def _user(self, n):
        return User.objects.create(username=f'sitter{n}')

    def test_two_people_sitting_together_get_different_papers(self):
        """The CFO's exact scenario. Round-robin guarantees it; random does not."""
        a = issue_attempt(self.course, user=self._user(1))
        b = issue_attempt(self.course, user=self._user(2))
        self.assertNotEqual(a.version_id, b.version_id)

    def test_the_next_fifteen_sittings_are_all_different_papers(self):
        seen = {issue_attempt(self.course, user=self._user(i)).version.number
                for i in range(15)}
        self.assertEqual(len(seen), 15)

    def test_a_resit_by_the_same_person_is_a_different_paper(self):
        u = self._user(99)
        first = issue_attempt(self.course, user=u)
        second = issue_attempt(self.course, user=u)
        self.assertNotEqual(first.version_id, second.version_id)
        self.assertEqual(second.attempt_no, 2)

    def test_two_sittings_of_the_SAME_paper_still_differ(self):
        """Even on one paper, question 3 is a different question for each of
        them, and the options sit in a different order."""
        u1, u2 = self._user(11), self._user(12)
        a = issue_attempt(self.course, user=u1)
        # Force the second sitting onto the same paper.
        b = issue_attempt(self.course, user=u2)
        b.version = a.version
        from hris.training_service import _shuffle_options, _shuffle_questions
        b.option_order = _shuffle_options(b.version, b)
        b.question_order = _shuffle_questions(b.version, b)
        b.save()

        pa, pb = paper_for(a), paper_for(b)
        self.assertNotEqual([q['id'] for q in pa], [q['id'] for q in pb],
                            'question order must differ between sittings')
        self.assertNotEqual(pa[0]['options'], pb[0]['options'],
                            'option order must differ between sittings')

    def test_the_paper_never_reveals_which_option_is_correct(self):
        a = issue_attempt(self.course, user=self._user(21))
        for q in paper_for(a):
            self.assertNotIn('correct_index', q)
            self.assertNotIn('explanation', q)


class ScoringTests(TestCase):
    def setUp(self):
        self.course = make_course(pass_mark=80)
        build_versions(self.course, seed=5)
        self.user = User.objects.create(username='marker')

    def _all_correct(self, attempt) -> dict:
        """Answer every question right, expressed in THIS sitting's own shown
        positions — which is the only way an answer sheet makes sense."""
        answers = {}
        for qid, positions in attempt.option_order.items():
            q = TrainingQuestion.objects.get(id=qid)
            answers[qid] = positions.index(q.correct_index)
        return answers

    def test_a_perfect_paper_passes_and_mints_a_certificate(self):
        a = issue_attempt(self.course, user=self.user)
        a = score_attempt(a, self._all_correct(a))
        self.assertTrue(a.passed)
        self.assertEqual(a.percent, 100)
        self.assertIsNotNone(getattr(a, 'certificate', None))
        self.assertTrue(a.certificate.serial.startswith('AD-IND-'))

    def test_one_candidates_answer_sheet_does_not_work_for_another(self):
        """The whole point. Copying "3=B, 4=A, ..." from a colleague must not
        reproduce their mark."""
        a = issue_attempt(self.course, user=self.user)
        other = issue_attempt(self.course, user=User.objects.create(username='friend'))
        leaked = self._all_correct(other)          # a perfect sheet, for THEIR sitting
        a = score_attempt(a, leaked)
        self.assertLess(a.percent, 80,
                        'a copied answer sheet should not pass another sitting')

    def test_below_the_pass_mark_fails_and_mints_no_certificate(self):
        a = issue_attempt(self.course, user=self.user)
        answers = self._all_correct(a)
        # Get 10 of 30 wrong by shifting the chosen option.
        for qid in list(answers)[:10]:
            answers[qid] = (answers[qid] + 1) % 4
        a = score_attempt(a, answers)
        self.assertFalse(a.passed)
        self.assertEqual(a.score, 20)
        self.assertIsNone(getattr(a, 'certificate', None))

    def test_a_certificate_is_issued_once_not_twice(self):
        a = issue_attempt(self.course, user=self.user)
        a = score_attempt(a, self._all_correct(a))
        first = a.certificate.id
        from hris.training_service import issue_certificate
        again = issue_certificate(a)
        self.assertEqual(first, again.id)

    def test_a_paper_submitted_after_the_time_limit_is_flagged(self):
        a = issue_attempt(self.course, user=self.user)
        TrainingAttempt.objects.filter(pk=a.pk).update(
            started_at=timezone.now() - timezone.timedelta(minutes=90))
        a.refresh_from_db()
        a = score_attempt(a, self._all_correct(a))
        self.assertTrue(a.late_submission)


class DwellGateTests(TestCase):
    def setUp(self):
        from hris.training_models import TrainingSlide
        self.course = make_course(min_dwell_seconds=900)
        for i in range(1, 6):
            TrainingSlide.objects.create(course=self.course, order=i,
                                         title=f'Slide {i}', est_seconds=60)

    def test_exam_stays_shut_until_every_slide_is_opened(self):
        ok, why = exam_unlocked(self.course, slides_seen=[1, 2, 3],
                                dwell_seconds=99999)
        self.assertFalse(ok)
        self.assertIn('all 5 slides', why)

    def test_exam_stays_shut_until_enough_time_is_spent(self):
        ok, why = exam_unlocked(self.course, slides_seen=[1, 2, 3, 4, 5],
                                dwell_seconds=60)
        self.assertFalse(ok)
        self.assertIn('longer', why)

    def test_exam_opens_when_both_conditions_are_met(self):
        ok, why = exam_unlocked(self.course, slides_seen=[1, 2, 3, 4, 5],
                                dwell_seconds=900)
        self.assertTrue(ok, why)


class AIValidationTests(TestCase):
    """The generated-question guards. The last one is what keeps an invented
    rule from being taught as company policy."""

    SOURCE = _norm('7.6.3 Employees are entitled to 20 days sick leave per '
                   'annum. It is important to note that sick leave cannot be '
                   'accrued.')
    CLAUSES = {'7.6.3'}

    def good(self, **over):
        q = {'stem': 'How many days sick leave per year?',
             'options': ['20', '15', '30', '10'], 'correct_index': 0,
             'section_ref': '7.6.3',
             'source_quote': 'Employees are entitled to 20 days sick leave per annum.'}
        q.update(over)
        return q

    def test_a_good_question_is_accepted(self):
        ok, why = validate_question(self.good(), self.CLAUSES, self.SOURCE)
        self.assertTrue(ok, why)

    def test_a_quote_that_is_not_in_the_document_is_rejected(self):
        ok, why = validate_question(
            self.good(source_quote='Employees are entitled to 40 days sick leave.'),
            self.CLAUSES, self.SOURCE)
        self.assertFalse(ok)
        self.assertIn('not in the document', why)

    def test_a_question_with_no_quote_is_rejected(self):
        ok, why = validate_question(self.good(source_quote=''), self.CLAUSES,
                                    self.SOURCE)
        self.assertFalse(ok)
        self.assertIn('no source quote', why)

    def test_line_breaks_in_the_pdf_do_not_defeat_the_quote_match(self):
        ok, why = validate_question(
            self.good(source_quote='Employees are entitled to 20 days\n  sick '
                                   'leave   per annum.'),
            self.CLAUSES, self.SOURCE)
        self.assertTrue(ok, why)

    def test_duplicate_options_are_rejected(self):
        ok, why = validate_question(self.good(options=['20', '20', '30', '10']),
                                    self.CLAUSES, self.SOURCE)
        self.assertFalse(ok)
        self.assertIn('duplicate', why)

    def test_wrong_option_count_is_rejected(self):
        ok, why = validate_question(self.good(options=['20', '15', '30']),
                                    self.CLAUSES, self.SOURCE)
        self.assertFalse(ok)

    def test_all_of_the_above_is_rejected(self):
        ok, why = validate_question(
            self.good(options=['20', '15', '30', 'All of the above']),
            self.CLAUSES, self.SOURCE)
        self.assertFalse(ok)

    def test_a_clause_not_in_the_document_is_rejected(self):
        ok, why = validate_question(self.good(section_ref='99.9'), self.CLAUSES,
                                    self.SOURCE)
        self.assertFalse(ok)
        self.assertIn('99.9', why)

    def test_a_slide_citing_a_clause_that_does_not_exist_is_rejected(self):
        ok, why = validate_slide({'title': 'Sick leave', 'bullets': ['x'],
                                  'section_ref': '99.9'}, self.CLAUSES)
        self.assertFalse(ok)


class SeededCourseTests(TestCase):
    """The real induction course, from the JSON that ships with the repo."""

    def test_the_seeder_builds_a_publishable_course(self):
        from django.core.management import call_command
        call_command('seed_induction_course', '--commit', '--republish',
                     verbosity=0)
        course = TrainingCourse.objects.get(is_induction=True)
        self.assertEqual(course.status, TrainingCourse.Status.PUBLISHED)
        self.assertEqual(course.versions.count(), 15)
        self.assertEqual(course.pass_mark, 80)
        self.assertGreaterEqual(course.questions.count(), 120)
        self.assertGreater(course.slides.count(), 20)

    def test_the_deck_really_is_thirty_minutes(self):
        from django.core.management import call_command
        call_command('seed_induction_course', '--commit', verbosity=0)
        course = TrainingCourse.objects.get(is_induction=True)
        total = sum(s.est_seconds for s in course.slides.all())
        self.assertAlmostEqual(total, course.duration_minutes * 60, delta=60)

    def test_the_right_answer_is_not_always_the_first_option(self):
        """The source JSON is authored answer-first for readability. If the
        seeder did not shuffle, every answer would be option A."""
        from django.core.management import call_command
        call_command('seed_induction_course', '--commit', verbosity=0)
        course = TrainingCourse.objects.get(is_induction=True)
        firsts = course.questions.filter(correct_index=0).count()
        total = course.questions.count()
        self.assertLess(firsts, total * 0.45,
                        'the answer key looks predictable')


class PanelFixTests(TestCase):
    """Regressions for the five issues the /fabe judge panel raised on this diff."""

    def test_a_bank_too_small_for_the_target_says_so_on_the_course(self):
        """H6 — the builder used to return an over-overlapping paper in silence,
        so "15 different papers" was a claim nobody could check."""
        course = make_course(bank=35, slug='tight')
        build_versions(course, seed=1)
        course.refresh_from_db()
        self.assertIn('share more than', course.ai_notes)
        self.assertIn('add more questions', course.ai_notes.lower())

    def test_a_healthy_bank_leaves_no_warning(self):
        course = make_course(bank=180, slug='roomy')
        build_versions(course, seed=1)
        course.refresh_from_db()
        self.assertNotIn('share more than', course.ai_notes)

    def test_deleting_a_question_does_not_inflate_a_mark(self):
        """H6 — scoring used to divide by however many questions still loaded,
        so removing one from the bank quietly raised everyone's percentage."""
        course = make_course(pass_mark=80, slug='denominator')
        build_versions(course, seed=4)
        u = User.objects.create(username='denom')
        a = issue_attempt(course, user=u)
        answers = {}
        for qid, positions in a.option_order.items():
            q = TrainingQuestion.objects.get(id=qid)
            answers[qid] = positions.index(q.correct_index)
        # Drop 6 of the 30 questions from the bank AFTER the paper was issued.
        doomed = list(a.option_order)[:6]
        TrainingQuestion.objects.filter(id__in=doomed).delete()
        for qid in doomed:
            answers.pop(qid, None)
        a = score_attempt(a, answers)
        self.assertEqual(a.score, 24)
        self.assertEqual(a.percent, 80, 'must divide by the 30 questions issued, '
                                        'not the 24 that still load')

    def test_a_slide_duration_that_is_not_a_number_is_rejected(self):
        """H73 — int('sixty') used to raise AFTER the course row was saved."""
        ok, why = validate_slide({'title': 'Leave', 'bullets': ['x'],
                                  'seconds': 'sixty'}, set())
        self.assertFalse(ok)
        self.assertIn('not a number', why)

    def test_an_absurd_slide_duration_is_rejected(self):
        ok, why = validate_slide({'title': 'Leave', 'bullets': ['x'],
                                  'seconds': 99999}, set())
        self.assertFalse(ok)
        self.assertIn('out of range', why)

    def test_a_sane_slide_duration_passes(self):
        ok, why = validate_slide({'title': 'Leave', 'bullets': ['x'],
                                  'seconds': 60}, set())
        self.assertTrue(ok, why)


class EndToEndApiTests(TestCase):
    """Walk the REAL path a new joiner walks: read the slides, start the exam,
    submit it, collect the certificate.

    The panel found a bug here that every unit test missed: `save_progress`
    parks an attempt row with no version to hold slides_seen and dwell, and the
    resume query matched it — so pressing "Start the exam" returned that row as
    a resumed paper with zero questions in it. Nothing below passes if that
    comes back.
    """

    def setUp(self):
        from hris.training_models import TrainingSlide
        self.course = make_course(bank=120, pass_mark=80, slug='e2e',
                                  min_dwell_seconds=40)
        for i in range(1, 4):
            TrainingSlide.objects.create(course=self.course, order=i,
                                         title=f'Slide {i}', est_seconds=30)
        build_versions(self.course, seed=9)
        self.user = User.objects.create_user('joiner', password='x')
        self.client.force_login(self.user)

    def _progress(self, seconds, slides):
        return self.client.post(
            f'/hris/api/training/courses/{self.course.slug}/progress/',
            data={'seconds': seconds, 'slides_seen': slides},
            content_type='application/json')

    def _read_for(self, seconds, slides=(1, 2, 3)):
        """Simulate a person actually reading for `seconds`.

        Credit is bounded by the wall clock since the row was last touched, so a
        test cannot just post faster — it has to move time. Backdating
        `updated_at` between beats is the honest stand-in for a reader sitting
        there, and it keeps the anti-burst guard under test rather than
        working around it.
        """
        r = None
        left = seconds
        while left > 0:
            step = min(20, left)
            TrainingAttempt.objects.filter(
                course=self.course, user=self.user, submitted_at__isnull=True,
                version__isnull=True,
            ).update(updated_at=timezone.now() - timezone.timedelta(seconds=step))
            r = self._progress(step, list(slides))
            left -= step
        return r

    def test_the_whole_journey_works(self):
        # Read the slides for the full gate (40s on this course).
        r = self._read_for(60)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['exam_unlocked'], r.json())

        start = self.client.post(
            f'/hris/api/training/courses/{self.course.slug}/start/',
            data={}, content_type='application/json')
        self.assertEqual(start.status_code, 200, start.content)
        paper = start.json()['paper']
        self.assertEqual(len(paper), 30,
                         'the exam came back empty — the resume query matched '
                         'the slide-progress row again')
        self.assertTrue(all(len(q['options']) == 4 for q in paper))

        # Answer everything correctly, in this sitting's own shown positions.
        attempt = TrainingAttempt.objects.get(id=start.json()['attempt_id'])
        answers = {}
        for qid, positions in attempt.option_order.items():
            q = TrainingQuestion.objects.get(id=qid)
            answers[qid] = positions.index(q.correct_index)

        sub = self.client.post(
            f'/hris/api/training/attempts/{attempt.id}/submit/',
            data={'answers': answers}, content_type='application/json')
        self.assertEqual(sub.status_code, 200, sub.content)
        body = sub.json()
        self.assertTrue(body['passed'], body)
        self.assertEqual(body['percent'], 100)

        pdf = self.client.get(
            f'/hris/api/training/certificates/{body["certificate_id"]}/pdf/')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')

    def test_the_exam_will_not_open_before_the_slides_are_read(self):
        r = self.client.post(
            f'/hris/api/training/courses/{self.course.slug}/start/',
            data={}, content_type='application/json')
        self.assertEqual(r.status_code, 409)
        self.assertIn('slides', r.json()['detail'].lower())

    def test_one_huge_heartbeat_cannot_skip_the_reading_gate(self):
        r = self._progress(999999, [1, 2, 3])
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['exam_unlocked'],
                         'a single enormous heartbeat unlocked the exam')
        self.assertLessEqual(r.json()['dwell_seconds'], 20)

    def test_another_persons_certificate_is_refused(self):
        self._read_for(60)
        start = self.client.post(
            f'/hris/api/training/courses/{self.course.slug}/start/',
            data={}, content_type='application/json')
        attempt = TrainingAttempt.objects.get(id=start.json()['attempt_id'])
        answers = {qid: pos.index(TrainingQuestion.objects.get(id=qid).correct_index)
                   for qid, pos in attempt.option_order.items()}
        sub = self.client.post(f'/hris/api/training/attempts/{attempt.id}/submit/',
                               data={'answers': answers},
                               content_type='application/json')
        cert_id = sub.json()['certificate_id']

        self.client.force_login(User.objects.create_user('nosy', password='x'))
        r = self.client.get(f'/hris/api/training/certificates/{cert_id}/pdf/')
        self.assertEqual(r.status_code, 403)


class GateFixTests(TestCase):
    """Regressions for what the /fabe ship gate proved with attack probes."""

    def setUp(self):
        from hris.training_models import TrainingSlide
        self.course = make_course(bank=120, pass_mark=80, slug='gate',
                                  min_dwell_seconds=40)
        for i in range(1, 4):
            TrainingSlide.objects.create(course=self.course, order=i,
                                         title=f'Slide {i}', est_seconds=30)
        build_versions(self.course, seed=12)
        self.user = User.objects.create_user('gatee', password='x')
        self.client.force_login(self.user)

    def test_fifty_instant_heartbeats_do_not_unlock_the_exam(self):
        """The reading gate was defeated by a loop: each beat was capped at 20s
        but nothing capped beats per second, so 50 instant posts credited 1000s.
        Credit is now bounded by the wall clock."""
        for _ in range(50):
            r = self.client.post(
                f'/hris/api/training/courses/{self.course.slug}/progress/',
                data={'seconds': 20, 'slides_seen': [1, 2, 3]},
                content_type='application/json')
        self.assertLess(r.json()['dwell_seconds'], 40,
                        'a burst of heartbeats bought real credit')
        self.assertFalse(r.json()['exam_unlocked'])
        start = self.client.post(
            f'/hris/api/training/courses/{self.course.slug}/start/',
            data={}, content_type='application/json')
        self.assertEqual(start.status_code, 409)

    def test_republishing_is_refused_while_someone_is_sitting(self):
        """Rebuilding papers nulls a live candidate's version (SET_NULL) and
        scores them zero while burning an attempt."""
        from hris.models import HRISProfile  # noqa: F401
        issue_attempt(self.course, user=User.objects.create(username='sitting'))
        hr = User.objects.create_superuser('hrboss', 'hr@x.com', 'x')
        self.client.force_login(hr)
        r = self.client.post(
            f'/hris/api/training/courses/{self.course.slug}/publish/',
            data={}, content_type='application/json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertIn('sitting this exam right now', r.json()['detail'])

    def test_a_question_with_three_options_is_marked_correctly(self):
        """The stored permutation is always 4 long. A 3-option question was
        rendered truncated but scored against the full permutation, so the right
        answer was marked wrong."""
        course = make_course(bank=40, per=1, versions=1, slug='threeopt')
        q = course.questions.first()
        course.questions.exclude(pk=q.pk).update(is_active=False)
        q.options = ['right', 'wrong a', 'wrong b']
        q.correct_index = 0
        q.save()
        build_versions(course, seed=1)
        a = issue_attempt(course, user=User.objects.create(username='threeo'))
        # Pin the shuffle. It is seeded off a random uuid4, and the OLD code
        # happened to mark correctly in 12 of the 24 permutations — so without
        # this the test went red on revert only half the time, which is not a
        # proof of anything. [3, 2, 1, 0] is one the old code got wrong.
        a.option_order = {str(q.id): [3, 2, 1, 0]}
        a.save(update_fields=['option_order'])
        shown = paper_for(a)[0]['options']
        self.assertEqual(len(shown), 3)
        a = score_attempt(a, {str(q.id): shown.index('right')})
        self.assertEqual(a.score, 1, 'clicking the right answer was marked wrong')

    def test_a_second_submit_of_the_same_paper_is_refused(self):
        for _ in range(3):
            self.client.post(
                f'/hris/api/training/courses/{self.course.slug}/progress/',
                data={'seconds': 20, 'slides_seen': [1, 2, 3]},
                content_type='application/json')
        TrainingAttempt.objects.filter(course=self.course, user=self.user).update(
            dwell_seconds=9999)
        start = self.client.post(
            f'/hris/api/training/courses/{self.course.slug}/start/',
            data={}, content_type='application/json')
        aid = start.json()['attempt_id']
        first = self.client.post(f'/hris/api/training/attempts/{aid}/submit/',
                                 data={'answers': {}},
                                 content_type='application/json')
        self.assertEqual(first.status_code, 200)
        second = self.client.post(f'/hris/api/training/attempts/{aid}/submit/',
                                  data={'answers': {}},
                                  content_type='application/json')
        self.assertEqual(second.status_code, 409)


class CertificateSignatureTests(TestCase):
    """CFO 20-Sep: the AML certificate had Chief Financial Officer and
    Compliance Officer signature lines, and merging the two designs dropped
    them. Both certificates carry them now — a training record for a regulated
    firm names who stands behind it."""

    def _pdf_text(self, pdf: bytes) -> str:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf)) as d:
            return '\n'.join((p.extract_text() or '') for p in d.pages)

    def test_the_induction_certificate_is_signed(self):
        from hris.training_certificate import render_certificate
        text = self._pdf_text(render_certificate(
            holder_name='Kelvin Kimani', course_title='Induction', percent=90,
            score=27, total=30, serial='AD-IND-TEST', verify_code='ABCD1234'))
        self.assertIn('Chief Financial Officer', text)
        self.assertIn('Compliance Officer', text)

    def test_it_does_not_claim_no_signature_is_required(self):
        """The old wording sat directly above two signature lines."""
        from hris.training_certificate import render_certificate
        text = self._pdf_text(render_certificate(
            holder_name='Kelvin Kimani', course_title='Induction', percent=90,
            serial='AD-IND-TEST', verify_code='ABCD1234'))
        self.assertNotIn('No signature is required', text)

    def test_signatories_can_be_overridden_per_course(self):
        from hris.training_certificate import render_certificate
        text = self._pdf_text(render_certificate(
            holder_name='X', course_title='Y', percent=90, serial='S',
            signatories=('Head of Human Capital', 'Line Manager')))
        self.assertIn('Head of Human Capital', text)
        self.assertNotIn('Compliance Officer', text)
