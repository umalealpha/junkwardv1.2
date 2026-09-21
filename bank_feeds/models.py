"""
bank_feeds/models.py

Automated bank-statement feed — v1 backbone.

Distinct from `fnb/` (real-time API + webhooks for payments). This app
handles the *statement-pull* side: a periodic poll (SFTP, daily file
drop, future bank-API) that pulls statement files, parses them, and
creates BankStatement / BankStatementLine records in the existing
banking app.

Models:
  • BankFeedConfig  — one row per bank+account combination we pull from
                      (e.g. "FNB BWP operating, daily SFTP at 06:00").
                      Credentials live in env vars (e.g.
                      BANK_FEED_FNB_SFTP_HOST), NOT on this row.
  • BankFeedRun     — one row per attempted pull. Records start, end,
                      counts, errors, parsed file hash for idempotency.

The actual SFTP / API protocol clients are deliberately stubbed in
services.py — each bank speaks something different (FNB CSV via SFTP,
Stanbic OFX, ABSA proprietary). Plug in per-bank parsers as they're
specified.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel


class BankFeedConfig(AuditableMixin, BaseModel):
    """How to pull statements from one bank account, periodically."""

    class Protocol(models.TextChoices):
        SFTP_CSV     = 'sftp_csv',     'SFTP — CSV file drop'
        SFTP_OFX     = 'sftp_ofx',     'SFTP — OFX file drop'
        SFTP_BAI2    = 'sftp_bai2',    'SFTP — BAI2 file drop'
        API_FNB      = 'api_fnb',      'Live API (FNB Botswana)'
        MANUAL       = 'manual',       'Manual upload (no automation)'

    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        PAUSED   = 'paused',   'Paused'
        ERROR    = 'error',    'Error'

    name = models.CharField(
        max_length=120,
        help_text='Friendly label, e.g. "FNB BWP operating — daily 06:00".',
    )
    bank_account = models.ForeignKey(
        'ledger.Account', on_delete=models.PROTECT,
        related_name='bank_feed_configs',
        help_text='Which GL bank account this feed populates.',
    )
    protocol = models.CharField(max_length=16, choices=Protocol.choices)

    # Connection details — non-sensitive metadata only.
    # Credentials are read from env vars at run time, keyed by env_prefix.
    env_prefix = models.CharField(
        max_length=40,
        help_text='Env-var prefix that holds the credentials. E.g. '
                  '"BANK_FEED_FNB_BWP" reads BANK_FEED_FNB_BWP_HOST, '
                  '_USERNAME, _PASSWORD or _PRIVATE_KEY_PATH.',
    )
    remote_path = models.CharField(
        max_length=240, blank=True,
        help_text='SFTP folder or API path to pull from.',
    )
    file_pattern = models.CharField(
        max_length=120, blank=True,
        default='*.csv',
        help_text='Glob to match statement files in remote_path.',
    )
    schedule_cron = models.CharField(
        max_length=60, blank=True,
        default='0 6 * * *',
        help_text='Cron expression for the scheduler. Default 06:00 daily.',
    )

    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(
        max_length=20, blank=True,
        help_text='Outcome of the most recent run.',
    )

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PAUSED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        unique_together = [('bank_account', 'env_prefix')]

    def __str__(self):
        return self.name


class BankFeedRun(BaseModel):
    """Audit record of every pull attempt — success or failure."""

    class Outcome(models.TextChoices):
        SUCCESS = 'success', 'Success'
        EMPTY   = 'empty',   'Empty (no new files)'
        ERROR   = 'error',   'Error'

    config = models.ForeignKey(
        BankFeedConfig, on_delete=models.CASCADE, related_name='runs',
    )
    triggered_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bank_feed_runs_triggered',
        help_text='User who manually triggered this run, or null for cron.',
    )

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=10, choices=Outcome.choices, default=Outcome.SUCCESS)

    files_seen = models.PositiveIntegerField(default=0)
    files_processed = models.PositiveIntegerField(default=0)
    statements_created = models.PositiveIntegerField(default=0)
    lines_created = models.PositiveIntegerField(default=0)
    duplicates_skipped = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)

    file_hashes = models.JSONField(
        default=list, blank=True,
        help_text='SHA-256 of every processed file — gives idempotency.',
    )

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['outcome']),
            models.Index(fields=['config', 'started_at']),
        ]

    def __str__(self):
        return f'{self.config.name} @ {self.started_at:%Y-%m-%d %H:%M} — {self.outcome}'
