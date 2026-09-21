"""
core/manual_models.py

Backend store behind the /help "What's New" section of the Omni User Manual.

The nine how-to chapters in the manual are stable, hand-written prose living
in the frontend page. The *feature log* — what we shipped and when — changes
every time we deploy, so it can't be hard-coded. This module holds it in the
DB so the nightly `update_user_manual` command can append to it without a
frontend redeploy. The /help page fetches it live.

Populated by: core/management/commands/update_user_manual.py (nightly cron).
Served by:    core/manual_views.py  (GET /api/v1/manual/whats-new/).
"""

from django.db import models

from .models import BaseModel


class ManualFeatureEntry(BaseModel):
    """One plain-English "What's New" note, generated from a feat: commit.

    Deduped by `commit_sha` so re-running the nightly job over an overlapping
    commit window never double-lists a feature.
    """

    commit_sha  = models.CharField(max_length=40, unique=True, db_index=True)
    commit_date = models.DateField(db_index=True)
    # Plain-English, user-facing. `title` is the headline, `summary` the 1-2
    # sentence "what you can now do" written for non-technical staff.
    title       = models.CharField(max_length=200)
    summary     = models.TextField(blank=True, default='')
    # Which part of omni it touches — free text label shown as a tag
    # (e.g. "HRIS", "Payments", "Health Care"). Best-effort from the commit.
    area        = models.CharField(max_length=60, blank=True, default='')
    # The raw commit subject, kept for audit / debugging (never shown to users).
    raw_subject = models.CharField(max_length=300, blank=True, default='')
    # False hides an entry from the manual without deleting it (e.g. a chore
    # that slipped through, or something the CFO wants pulled).
    published   = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['-commit_date', '-created_at']
        verbose_name = 'Manual feature entry'
        verbose_name_plural = 'Manual feature entries'

    def __str__(self):
        return f"{self.commit_date} · {self.title}"


class ManualUpdateRun(BaseModel):
    """One nightly manual-update run — observability + a visible 'last updated'.

    One row per calendar day (update_or_create on run_date), so the /help page
    can show when the feature log was last refreshed.
    """

    run_date        = models.DateField(unique=True, db_index=True)
    commits_seen    = models.IntegerField(default=0)
    entries_added   = models.IntegerField(default=0)
    last_commit_sha = models.CharField(max_length=40, blank=True, default='')
    notes           = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-run_date']
        verbose_name = 'Manual update run'
        verbose_name_plural = 'Manual update runs'

    def __str__(self):
        return f"{self.run_date} · +{self.entries_added}"
