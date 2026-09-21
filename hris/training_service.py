"""hris/training_service.py — building papers, handing them out, marking them.

The anti-cheat rules live here, in one place, so a test can prove each of them:

  build_versions(course)   pre-builds N different papers out of the bank.
  issue_attempt(...)       hands the NEXT paper out, round-robin, and shuffles
                           this candidate's options.
  score_attempt(...)       marks it, and mints the certificate on a pass.

"Round-robin" is deliberate rather than random: random assignment can hand the
same paper to two people sitting next to each other, which is exactly the
scenario the CFO described. A counter on the course cannot.
"""
from __future__ import annotations

import logging
import random
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from hris.training_models import (ExamVersion, TrainingAttempt, TrainingCertificate,
                                  TrainingCourse, TrainingQuestion)


log = logging.getLogger(__name__)


class NotEnoughQuestions(Exception):
    """The bank is too small to build a paper of the requested size."""


# No two papers may share more than this many questions. Set-equality alone is
# not enough — two papers sharing 29 of 30 are the same paper to a cheat.
MAX_PAPER_OVERLAP = 12


# ---------------------------------------------------------------------------
# Building the papers
# ---------------------------------------------------------------------------

def build_versions(course: TrainingCourse, *, seed: int | None = None) -> list[ExamVersion]:
    """(Re)build this course's pre-set papers from its ACTIVE question bank.

    Every paper is a different draw of `questions_per_paper` questions in a
    different order. With a bank meaningfully larger than one paper, no two
    papers share the same set; where the bank is only just big enough, the sets
    overlap but the ORDER always differs, so "the answer to question 3" still
    does not travel.

    Deterministic when `seed` is given — the tests rely on that.
    """
    bank = list(course.questions.filter(is_active=True).order_by('id'))
    per = course.questions_per_paper
    if len(bank) < per:
        raise NotEnoughQuestions(
            f'{course.title}: the bank holds {len(bank)} active questions but a '
            f'paper needs {per}. Add more questions before publishing.'
        )

    rng = random.Random(seed if seed is not None else f'{course.slug}:{len(bank)}')
    seen: set[frozenset] = set()
    built: list[ExamVersion] = []
    shortfall: list[int] = []      # local, not a module global — this runs on
                                   # gunicorn threads and two HR users may
                                   # publish at the same instant.

    with transaction.atomic():
        course.versions.all().delete()
        for number in range(1, course.version_count + 1):
            picked, worst = _draw_distinct(bank, per, rng, seen)
            if worst > MAX_PAPER_OVERLAP:
                shortfall.append(worst)
            built.append(ExamVersion.objects.create(
                course=course, number=number,
                question_ids=[str(q.id) for q in picked],
            ))
    if shortfall:
        course.ai_notes = (
            (course.ai_notes + '\n' if course.ai_notes else '')
            + f'⚠ {len(shortfall)} of {len(built)} papers share more than '
              f'{MAX_PAPER_OVERLAP} questions with another paper (worst '
              f'{max(shortfall)}). The bank of {len(bank)} is small for '
              f'{len(built)} papers of {per} — add more questions.')
        course.save(update_fields=['ai_notes', 'updated_at'])
    return built


def _draw_distinct(bank: list[TrainingQuestion], per: int, rng: random.Random,
                   seen: set[frozenset]) -> tuple[list[TrainingQuestion], int]:
    """Draw `per` questions, overlapping as little as possible with earlier papers.

    Two papers may share at most `MAX_PAPER_OVERLAP` questions. Set-equality
    alone is not enough: two papers that share 29 of 30 questions are, for
    cheating purposes, the same paper. Tries hard for a low-overlap draw and
    falls back to the best of what it found rather than failing outright — with
    a small bank a perfect spread is not always possible, and the per-sitting
    question and option shuffles still carry the anti-cheat load.
    """
    best, best_overlap = None, None
    for _ in range(120):
        picked = rng.sample(bank, per)
        key = frozenset(str(q.id) for q in picked)
        worst = max((len(key & prior) for prior in seen), default=0)
        if best_overlap is None or worst < best_overlap:
            best, best_overlap = picked, worst
        if worst <= MAX_PAPER_OVERLAP:
            break
    if best_overlap is not None and best_overlap > MAX_PAPER_OVERLAP:
        # Say it out loud. Returning a weak paper in silence is how "15
        # different papers" becomes a claim nobody can rely on.
        log.warning('training: could not build a paper under the %s-question '
                    'overlap target — best was %s, from a bank of %s for papers '
                    'of %s.', MAX_PAPER_OVERLAP, best_overlap, len(bank), per)
    seen.add(frozenset(str(q.id) for q in best))
    rng.shuffle(best)
    return best, (best_overlap or 0)


# ---------------------------------------------------------------------------
# Handing a paper out
# ---------------------------------------------------------------------------

def issue_attempt(course: TrainingCourse, *, user=None, employee=None,
                  dwell_seconds: int = 0, slides_seen: Iterable | None = None
                  ) -> TrainingAttempt:
    """Hand the next paper to this candidate and shuffle their options.

    The course row is locked while the counter moves, so two people pressing
    Start at the same instant get two different papers rather than the same one.
    """
    with transaction.atomic():
        # Lock FIRST, then read the papers. Reading them before the lock lets a
        # concurrent publish rebuild the set underneath us, so the index would
        # point into a list that no longer exists.
        locked = TrainingCourse.objects.select_for_update().get(pk=course.pk)
        versions = list(locked.versions.order_by('number'))
        if not versions:
            raise NotEnoughQuestions(
                f'{course.title} has no papers built yet — publish the course first.'
            )
        index = locked.papers_issued % len(versions)
        locked.papers_issued = locked.papers_issued + 1
        locked.save(update_fields=['papers_issued', 'updated_at'])
        version = versions[index]

        prior = TrainingAttempt.objects.filter(course=course)
        prior = prior.filter(user=user) if user is not None else prior.filter(employee=employee)
        attempt_no = prior.count() + 1

        attempt = TrainingAttempt.objects.create(
            course=course, user=user, employee=employee, version=version,
            attempt_no=attempt_no, dwell_seconds=dwell_seconds,
            slides_seen=list(slides_seen or []),
        )
        attempt.option_order = _shuffle_options(version, attempt)
        attempt.question_order = _shuffle_questions(version, attempt)
        attempt.save(update_fields=['option_order', 'question_order', 'updated_at'])
    return attempt


def _shuffle_options(version: ExamVersion, attempt: TrainingAttempt) -> dict:
    """Per-sitting option shuffle, seeded off the attempt id so it is stable.

    Returns {question_id: [canonical_index, ...]} — position 0 of the list is the
    option shown first.
    """
    rng = random.Random(str(attempt.id))
    order = {}
    for qid in version.question_ids:
        positions = list(range(4))
        rng.shuffle(positions)
        order[str(qid)] = positions
    return order


def _shuffle_questions(version: ExamVersion, attempt: TrainingAttempt) -> list:
    """Per-sitting question order. Two people on the SAME paper still disagree
    about what "question 3" is, which is the exact thing the CFO described."""
    rng = random.Random('q:' + str(attempt.id))
    ids = [str(q) for q in version.question_ids]
    rng.shuffle(ids)
    return ids


def _sitting_order(attempt: TrainingAttempt) -> list:
    """The question ids in the order THIS candidate sees them."""
    if attempt.question_order:
        return [str(q) for q in attempt.question_order]
    return [str(q) for q in (attempt.version.question_ids if attempt.version else [])]


def _positions_for(question, attempt: TrainingAttempt, qid: str) -> list[int]:
    """This sitting's option order, clipped to the options the question really
    has. The stored permutation is always 4 long; a question with 3 options was
    rendered truncated but SCORED against the full permutation, so the candidate
    could click the right answer and be marked wrong. Clip in one place, used by
    both the render and the marking, so they can never disagree again."""
    stored = attempt.option_order.get(qid) or [0, 1, 2, 3]
    n = len(question.options or [])
    return [p for p in stored if p < n]


def paper_for(attempt: TrainingAttempt) -> list[dict]:
    """The paper exactly as this candidate must see it — no correct answers."""
    if not attempt.version:
        return []
    qs = {str(q.id): q for q in TrainingQuestion.objects.filter(
        id__in=attempt.version.question_ids)}
    out = []
    for n, qid in enumerate(_sitting_order(attempt), start=1):
        q = qs.get(str(qid))
        if not q:
            continue
        positions = _positions_for(q, attempt, str(qid))
        out.append({
            'id': str(q.id),
            'number': n,
            'stem': q.stem,
            'options': [q.options[p] for p in positions],
            'section_ref': q.section_ref,
        })
    return out


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------

def score_attempt(attempt: TrainingAttempt, answers: dict) -> TrainingAttempt:
    """Mark the paper. `answers` is {question_id: shown_index}.

    A shown index is translated back through this attempt's own option order
    before it is compared, so the same stored answer means different things to
    two candidates — which is the point.
    """
    course = attempt.course
    qs = {str(q.id): q for q in TrainingQuestion.objects.filter(
        id__in=(attempt.version.question_ids if attempt.version else []))}

    # Over the time limit? Mark it anyway and record the fact — a late paper is
    # information for HR, not an automatic fail.
    now = timezone.now()
    elapsed = (now - attempt.started_at).total_seconds()
    attempt.late_submission = elapsed > (course.time_limit_minutes * 60) + 120

    issued = _sitting_order(attempt)
    missing = [qid for qid in issued if qid not in qs]
    if missing:
        # The paper is the contract. A question deleted from the bank after the
        # paper was issued must not quietly shrink the denominator and hand the
        # candidate a higher percentage than they earned.
        log.error('training: attempt %s was issued %s questions but %s no longer '
                  'resolve (%s...) — marking against the issued count',
                  attempt.id, len(issued), len(missing), missing[:3])

    right = 0
    cleaned = {}
    for qid, shown in (answers or {}).items():
        q = qs.get(str(qid))
        if not q:
            continue
        try:
            shown_i = int(shown)
        except (TypeError, ValueError):
            # Not a number. Treat it as unanswered, but say so — silently
            # dropping it would cost the candidate a mark with nothing on
            # record to explain why if they query the result.
            log.warning('training: attempt %s sent a non-numeric answer %r for '
                        'question %s — marking it unanswered',
                        attempt.id, shown, qid)
            continue
        positions = _positions_for(q, attempt, str(qid))
        if not (0 <= shown_i < len(positions)):
            continue
        cleaned[str(qid)] = shown_i
        if positions[shown_i] == q.correct_index:
            right += 1

    total = len(issued) or course.questions_per_paper
    attempt.answers = cleaned
    attempt.score = right
    attempt.percent = round(right * 100 / total) if total else 0
    attempt.passed = attempt.percent >= course.pass_mark
    attempt.submitted_at = now
    with transaction.atomic():
        attempt.save(update_fields=['answers', 'score', 'percent', 'passed',
                                    'submitted_at', 'late_submission', 'updated_at'])
        if attempt.passed:
            issue_certificate(attempt)
        _sync_assignment(attempt)
    return attempt


def issue_certificate(attempt: TrainingAttempt) -> TrainingCertificate | None:
    """Mint the certificate for a passed attempt. Idempotent."""
    if not attempt.passed:
        return None
    existing = TrainingCertificate.objects.filter(attempt=attempt).first()
    if existing:
        return existing
    holder = ''
    if attempt.employee_id:
        holder = attempt.employee.full_name
    elif attempt.user_id:
        holder = (attempt.user.get_full_name() or attempt.user.username)
    return TrainingCertificate.objects.create(
        attempt=attempt,
        serial=TrainingCertificate.new_serial(),
        verify_code=TrainingCertificate.new_verify_code(),
        holder_name=holder,
        course_title=attempt.course.title,
    )


def _sync_assignment(attempt: TrainingAttempt) -> None:
    """Move the person's assignment row to passed / failed."""
    from hris.training_models import TrainingAssignment
    if not attempt.employee_id:
        return
    row = TrainingAssignment.objects.filter(
        course=attempt.course, employee_id=attempt.employee_id).first()
    if not row:
        return
    row.status = (TrainingAssignment.Status.PASSED if attempt.passed
                  else TrainingAssignment.Status.FAILED)
    row.completed_at = attempt.submitted_at if attempt.passed else None
    row.save(update_fields=['status', 'completed_at', 'updated_at'])


# ---------------------------------------------------------------------------
# The gate between the slides and the exam
# ---------------------------------------------------------------------------

def attempts_left(course: TrainingCourse, *, user=None, employee=None) -> int:
    """Sittings this person has left before HR has to unlock them."""
    from hris.training_models import TrainingAssignment
    used = TrainingAttempt.objects.filter(course=course, submitted_at__isnull=False)
    used = used.filter(user=user) if user is not None else used.filter(employee=employee)
    allowed = course.max_attempts
    if employee is not None:
        row = TrainingAssignment.objects.filter(course=course, employee=employee).first()
        if row:
            allowed += row.extra_attempts
    return max(0, allowed - used.count())


def exam_unlocked(course: TrainingCourse, *, slides_seen, dwell_seconds: int
                  ) -> tuple[bool, str]:
    """May this candidate start the exam yet?

    Two conditions, both from the CFO's brief that this is a THIRTY-MINUTE
    course and not a click-through: every slide opened, and enough time actually
    spent on them.
    """
    total_slides = course.slides.count()
    seen = {int(s) for s in (slides_seen or []) if str(s).isdigit()}
    if total_slides and len(seen) < total_slides:
        return False, f'Please read all {total_slides} slides first ({len(seen)} so far).'
    if dwell_seconds < course.min_dwell_seconds:
        left = course.min_dwell_seconds - dwell_seconds
        return False, f'Please spend a little longer on the material — about {max(1, left // 60)} more minute(s).'
    return True, ''
