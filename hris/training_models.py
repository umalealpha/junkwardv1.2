"""hris/training_models.py — Omni Induction & Training Academy (CFO 2026-09-20).

Dorothy Ikgopoleng personally walked every new joiner through the Conditions of
Service, the contract and the IT rules. That is a week of her month, and it does
not scale. This module turns that talk into a self-service 30-minute course:
branded slides in the browser, then a 30-question multiple-choice exam, then a
certificate that says the person read and understood the manual.

Two ways a course comes into being:

  * **Seeded** — the Induction course itself, authored against the real
    "Conditions of Service 2026 (ELRA 2025)" already in the HR Document Vault.
  * **Uploaded** — HR presses "Upload Here", drops in any PDF or Word file, and
    `training_ai.py` reads it and drafts the slides and the question bank. Every
    generated question is validated and left in DRAFT until HR publishes it.

Anti-cheat is the reason for most of the shape here. A colleague being asked
"what did you put for question 3?" must be useless, so:

  * the course carries a BANK of questions, far more than one paper needs;
  * `ExamVersion` rows are pre-built papers, each a different 30 drawn from that
    bank in a different order (the CFO asked for 10-15 — default 15);
  * papers are handed out ROUND-ROBIN off `TrainingCourse.papers_issued`, so the
    next 15 people to sit — including two people sitting side by side, and
    including a re-sit by the same person — all get different papers;
  * on top of that every sitting shuffles the four options per question with a
    seed stored on the attempt, so even two sittings of the same version do not
    share "the answer is B".

Nothing here touches money, and no employee data ever leaves for an outside
model — only the company document being taught (AD-POL-AI-GOV-001).
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from core.models import BaseModel


class TrainingCourse(BaseModel):
    """One course: a slide deck plus a question bank plus its pre-built papers."""

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED  = 'archived',  'Archived'

    class Source(models.TextChoices):
        SEEDED   = 'seeded',   'Authored in Omni'
        UPLOADED = 'uploaded', 'Generated from an uploaded document'

    title       = models.CharField(max_length=200)
    slug        = models.SlugField(max_length=80, unique=True)
    summary     = models.TextField(blank=True)
    status      = models.CharField(max_length=12, choices=Status.choices,
                                   default=Status.DRAFT)
    source      = models.CharField(max_length=12, choices=Source.choices,
                                   default=Source.SEEDED)

    # Where the content came from. `source_document` points at the HR vault copy
    # so a reader can always open the manual the course is drawn from.
    source_document = models.ForeignKey('hris.HRDocument', null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='induction_courses')
    source_filename = models.CharField(max_length=255, blank=True)
    source_chars    = models.PositiveIntegerField(default=0)

    # The shape of the course. Defaults are the CFO's brief: 30 minutes of
    # slides, then 30 questions, with 15 different papers in circulation.
    duration_minutes    = models.PositiveSmallIntegerField(default=30)
    min_dwell_seconds   = models.PositiveIntegerField(default=900)   # 15 min
    questions_per_paper = models.PositiveSmallIntegerField(default=30)
    version_count       = models.PositiveSmallIntegerField(default=15)
    # 80% — the certificate claims the holder understood the manual, so the bar
    # has to back that claim up. Re-sits are free and immediate.
    pass_mark           = models.PositiveSmallIntegerField(default=80)  # percent
    time_limit_minutes  = models.PositiveSmallIntegerField(default=45)
    # After this many failed sittings the person needs HR to unlock them — which
    # is the point at which a conversation beats another attempt.
    max_attempts        = models.PositiveSmallIntegerField(default=3)

    # The induction course every new joiner must pass. Exactly one may hold this.
    is_induction = models.BooleanField(default=False)

    # Provenance of an AI-drafted course, for the audit trail.
    ai_generated = models.BooleanField(default=False)
    ai_engine    = models.CharField(max_length=40, blank=True)
    ai_notes     = models.TextField(blank=True)

    # Round-robin counter — see the module docstring. Bumped under a row lock
    # every time a paper is handed out, so consecutive sitters differ.
    papers_issued = models.PositiveIntegerField(default=0)

    created_by   = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='induction_courses_created')
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-is_induction', 'title']

    def __str__(self) -> str:                       # pragma: no cover - admin only
        return self.title

    @property
    def pass_mark_questions(self) -> int:
        """How many right answers the pass mark actually needs, rounded up."""
        n = self.questions_per_paper
        return -(-(self.pass_mark * n) // 100)


class TrainingSlide(BaseModel):
    """One slide. `body_md` is a small markdown subset: bullets and paragraphs."""

    course      = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE,
                                    related_name='slides')
    order       = models.PositiveSmallIntegerField()
    title       = models.CharField(max_length=200)
    body_md     = models.TextField(blank=True)
    # Which clause of the source manual this slide teaches, e.g. "7.1" — shown
    # on the slide so a reader can always check the original wording.
    section_ref = models.CharField(max_length=40, blank=True)
    est_seconds = models.PositiveSmallIntegerField(default=60)

    class Meta:
        ordering = ['course', 'order']
        constraints = [
            models.UniqueConstraint(fields=['course', 'order'],
                                    name='uniq_slide_order_per_course'),
        ]


class TrainingQuestion(BaseModel):
    """One question in a course's bank. Four options, exactly one correct.

    `options` is a plain list of four strings in their CANONICAL order;
    `correct_index` indexes into that list. Each sitting shuffles a copy of the
    list, so the canonical order is never what the candidate sees.
    """

    course        = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE,
                                      related_name='questions')
    stem          = models.TextField()
    options       = models.JSONField(default=list)
    correct_index = models.PositiveSmallIntegerField(default=0)
    explanation   = models.TextField(blank=True)
    section_ref   = models.CharField(max_length=40, blank=True)
    topic         = models.CharField(max_length=60, blank=True)
    is_active     = models.BooleanField(default=True)
    # AI-drafted questions land inactive until HR approves the course.
    ai_generated  = models.BooleanField(default=False)

    class Meta:
        ordering = ['course', 'id']

    @property
    def correct_option(self) -> str:
        """The text of the right answer, or '' if this question is malformed.

        A bank row with no options is a data fault, not an exception path — the
        admin and the HR draft screen both need to render it so somebody can see
        it and fix it.
        """
        opts = self.options or []
        if 0 <= self.correct_index < len(opts):
            return str(opts[self.correct_index])
        return ''


class ExamVersion(BaseModel):
    """A pre-built paper: an ordered list of question ids drawn from the bank."""

    course       = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE,
                                     related_name='versions')
    number       = models.PositiveSmallIntegerField()
    question_ids = models.JSONField(default=list)

    class Meta:
        ordering = ['course', 'number']
        constraints = [
            models.UniqueConstraint(fields=['course', 'number'],
                                    name='uniq_exam_version_per_course'),
        ]

    def __str__(self) -> str:                       # pragma: no cover - admin only
        return f'{self.course.title} — Paper {self.number}'


class TrainingAssignment(BaseModel):
    """"You must do this course" — raised for a joiner, or for everyone."""

    class Status(models.TextChoices):
        ASSIGNED  = 'assigned',  'Assigned'
        STARTED   = 'started',   'Started'
        PASSED    = 'passed',    'Passed'
        FAILED    = 'failed',    'Failed — must re-sit'

    course      = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE,
                                    related_name='assignments')
    employee    = models.ForeignKey('payroll.Employee', on_delete=models.CASCADE,
                                    related_name='induction_assignments')
    status      = models.CharField(max_length=12, choices=Status.choices,
                                   default=Status.ASSIGNED)
    due_date    = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='induction_assignments_made')
    completed_at = models.DateTimeField(null=True, blank=True)
    # Sittings HR has granted on top of the course default, after the person ran
    # out. Recorded rather than resetting the count, so the history survives.
    extra_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['course', 'employee'],
                                    name='uniq_training_assignment'),
        ]


class TrainingAttempt(BaseModel):
    """One sitting of one paper by one person.

    `option_order` maps question id -> the shuffled positions shown to THIS
    candidate, e.g. {"<qid>": [2, 0, 3, 1]} means the option displayed first was
    canonical option 2. It is stored so a submitted answer can be scored, and so
    a disputed result can be reconstructed exactly as the candidate saw it.
    """

    course      = models.ForeignKey(TrainingCourse, on_delete=models.CASCADE,
                                    related_name='attempts')
    employee    = models.ForeignKey('payroll.Employee', null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='induction_attempts')
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='induction_attempts')
    version     = models.ForeignKey(ExamVersion, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='attempts')
    attempt_no  = models.PositiveSmallIntegerField(default=1)

    option_order   = models.JSONField(default=dict)
    # The question ids in the order THIS candidate saw them. Two people on the
    # same paper therefore disagree about what "question 3" is.
    question_order = models.JSONField(default=list)
    answers        = models.JSONField(default=dict)  # question id -> shown index
    # Submitted after the time limit ran out. Recorded, never used to fail
    # anyone on its own — HR sees it and decides.
    late_submission = models.BooleanField(default=False)

    slides_seen   = models.JSONField(default=list)
    dwell_seconds = models.PositiveIntegerField(default=0)

    started_at   = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score        = models.PositiveSmallIntegerField(default=0)   # right answers
    percent      = models.PositiveSmallIntegerField(default=0)
    passed       = models.BooleanField(default=False)

    class Meta:
        ordering = ['-started_at']

    @property
    def is_open(self) -> bool:
        return self.submitted_at is None


class TrainingCertificate(BaseModel):
    """Issued once, on a pass. The serial is what a third party verifies."""

    attempt   = models.OneToOneField(TrainingAttempt, on_delete=models.CASCADE,
                                     related_name='certificate')
    serial    = models.CharField(max_length=40, unique=True)
    # Short human-typeable code for the public verify page.
    verify_code = models.CharField(max_length=12, unique=True)
    holder_name = models.CharField(max_length=160)
    course_title = models.CharField(max_length=200)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-issued_at']

    @staticmethod
    def new_serial() -> str:
        return f'AD-IND-{uuid.uuid4().hex[:10].upper()}'

    @staticmethod
    def new_verify_code() -> str:
        # No I/O/0/1 — these get read out over the phone.
        import secrets
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        return ''.join(secrets.choice(alphabet) for _ in range(8))
