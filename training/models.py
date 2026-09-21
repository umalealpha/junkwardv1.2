"""
training/models.py

Compliance training the whole company must complete. Ships for AML in October
2026 (bug 5ef4cc79, Kakale Botana). Same shape as SOPDocument+SOPAcknowledgement
in iso_compliance, but adds a quiz gate: a user only counts as done when they
pass. The passing attempt mints a certificate number the PDF cites.

Deliberately small: three models, no LMS bloat.
"""
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BaseModel


class TrainingModule(BaseModel):
    """One publishable training module — e.g. 'AML 2026'."""

    slug              = models.SlugField(max_length=64, unique=True)
    title             = models.CharField(max_length=200)
    subtitle          = models.CharField(max_length=300, blank=True, default="")
    body_html         = models.TextField(
                            blank=True, default="",
                            help_text="Main lesson body. HTML allowed.",
                        )
    video_url         = models.URLField(blank=True, default="")
    pdf               = models.FileField(
                            upload_to="training/%Y/", blank=True, null=True,
                            help_text="Optional slide deck / handbook.",
                        )

    pass_mark_pct     = models.PositiveIntegerField(default=80)
    open_from         = models.DateField()
    open_until        = models.DateField()

    # Who must take it. NULL/empty = every active user must.
    mandatory_for_all = models.BooleanField(default=True)

    is_published      = models.BooleanField(default=False, db_index=True)
    created_by        = models.ForeignKey(
                            settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                            null=True, blank=True, related_name="training_modules_created",
                        )

    class Meta(BaseModel.Meta):
        ordering = ["-open_from", "title"]

    def __str__(self) -> str:
        return f"{self.title} ({self.slug})"

    def is_open_today(self) -> bool:
        today = timezone.localdate()
        return self.is_published and self.open_from <= today <= self.open_until


class TrainingQuestion(BaseModel):
    """One multiple-choice question. A/B/C/D. One is correct."""

    LETTER_CHOICES = [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")]

    module        = models.ForeignKey(
                        TrainingModule, on_delete=models.CASCADE,
                        related_name="questions",
                    )
    order         = models.PositiveIntegerField(default=0)
    prompt        = models.TextField()
    option_a      = models.CharField(max_length=500)
    option_b      = models.CharField(max_length=500)
    option_c      = models.CharField(max_length=500, blank=True, default="")
    option_d      = models.CharField(max_length=500, blank=True, default="")
    correct_letter = models.CharField(max_length=1, choices=LETTER_CHOICES)

    class Meta(BaseModel.Meta):
        ordering = ["order", "created_at"]

    def __str__(self) -> str:
        return f"Q{self.order}: {self.prompt[:60]}"

    def options_visible_to_trainee(self) -> list[dict]:
        rows = [
            {"letter": "A", "text": self.option_a},
            {"letter": "B", "text": self.option_b},
        ]
        if self.option_c:
            rows.append({"letter": "C", "text": self.option_c})
        if self.option_d:
            rows.append({"letter": "D", "text": self.option_d})
        return rows


class TrainingAttempt(BaseModel):
    """One attempt at one module by one user. Multiple rows per user allowed —
    the audit trail wants every attempt. `passed` is derived at submit time."""

    module        = models.ForeignKey(
                        TrainingModule, on_delete=models.PROTECT,
                        related_name="attempts",
                    )
    user          = models.ForeignKey(
                        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                        related_name="training_attempts",
                    )
    answers       = models.JSONField(
                        default=dict, blank=True,
                        help_text="{question_id: 'A'/'B'/'C'/'D'}",
                    )
    score_pct     = models.PositiveIntegerField(default=0)
    passed        = models.BooleanField(default=False, db_index=True)
    completed_at  = models.DateTimeField(default=timezone.now)

    # Set only on a passing attempt.
    certificate_number = models.CharField(
        max_length=32, blank=True, default="", db_index=True,
    )

    class Meta(BaseModel.Meta):
        ordering = ["-completed_at"]
        indexes = [
            models.Index(fields=["module", "user"]),
            models.Index(fields=["module", "passed"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} on {self.module_id}: {self.score_pct}%"

    def mint_certificate_number(self) -> str:
        """AML-2026-000123 style. Sequential within the module."""
        if self.certificate_number:
            return self.certificate_number
        n = (TrainingAttempt.objects
             .filter(module=self.module, passed=True)
             .exclude(pk=self.pk)
             .exclude(certificate_number="")
             .count()) + 1
        stem = (self.module.slug or "TRN").upper().replace("-", "")[:8]
        year = timezone.localdate().year
        self.certificate_number = f"{stem}-{year}-{n:06d}"
        return self.certificate_number
