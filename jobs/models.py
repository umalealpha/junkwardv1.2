"""
jobs/models.py — the scheduled-job switchboard (CFO 2026-09-09).

"another dashboard what are the crones running, and a switch i can turn on and
turn off and visile for my eyes only."

The switch lives HERE, in Omni's database, and nowhere else. It is a flag a job
checks before it runs. It deliberately CANNOT edit the host's schedule files —
a screen that could write those would be a screen that could run anything on the
server. So: the job still fires on its timer, and asks Omni "am I on?" first.

Why a DB flag and not the old way: the staff hours reminders were silently
switched off for nine days because that lived in a file the deploy script
rewrites. A flag the CFO owns takes effect at the next run with no deploy, and
survives every deploy because nothing on the host can reach in and change it.

`is_protected` is set from a CODE constant, never from this table, so no edit
here — and no bug on this screen — can ever unlock the backups.
"""
from __future__ import annotations

from django.db import models

from core.models import BaseModel

# Set from jobs.protected.PROTECTED at import time. The switch is refused for
# these; the wrapper ignores the flag for them too. Belt and braces.


class ScheduledJob(BaseModel):
    class Category(models.TextChoices):
        SENDS        = 'sends',        'Sends a message'
        MOVES_DATA   = 'moves-data',   'Moves or writes data'
        SAFETY       = 'safety',       'A safety net'
        REPORT       = 'report',       'Prepares a report'
        HOUSEKEEPING = 'housekeeping', 'Housekeeping'

    name          = models.CharField(max_length=80, unique=True)
    what_it_does  = models.TextField(blank=True, default='')
    who_it_affects = models.CharField(max_length=200, blank=True, default='')
    if_switched_off = models.TextField(blank=True, default='')
    category      = models.CharField(max_length=16, choices=Category.choices,
                                     default=Category.SENDS)
    schedule      = models.CharField(max_length=60, blank=True, default='')

    # The CFO's switch. True = the job may run.
    is_enabled    = models.BooleanField(default=True)
    off_until     = models.DateField(null=True, blank=True)
    off_reason    = models.CharField(max_length=200, blank=True, default='')

    # From code, refreshed by import_cron_state; never trusted from an edit here.
    is_protected  = models.BooleanField(default=False)

    # The host's own view, filled by the snapshot: is the file actually present.
    host_present  = models.BooleanField(default=True)
    host_disabled = models.BooleanField(default=False)   # renamed .DISABLED
    last_seen_at  = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['name']
        verbose_name = 'Scheduled job'

    def __str__(self):
        return self.name

    @property
    def effective_on(self) -> bool:
        """Will it actually run at its next tick?"""
        return self.is_enabled and self.host_present and not self.host_disabled

    def why(self) -> str:
        if self.is_protected and not self.is_enabled:
            return 'protected — cannot be switched off'
        if not self.is_enabled:
            u = f' until {self.off_until:%d %b}' if self.off_until else ''
            return f'you switched it off{u}'
        if self.host_disabled or not self.host_present:
            return 'switched on here, but not installed on the server'
        return 'running'


class JobRun(BaseModel):
    class Status(models.TextChoices):
        OK      = 'ok',      'Ran'
        FAILED  = 'failed',  'Failed'
        SKIPPED = 'skipped', 'Skipped — switched off'

    job         = models.ForeignKey(ScheduledJob, on_delete=models.CASCADE,
                                    related_name='runs')
    started_at  = models.DateTimeField(db_index=True)
    ended_at    = models.DateTimeField(null=True, blank=True)
    status      = models.CharField(max_length=8, choices=Status.choices)
    exit_code   = models.IntegerField(null=True, blank=True)
    output_tail = models.TextField(blank=True, default='')     # capped, no PII
    switch_was_on = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.job_id} {self.started_at:%Y-%m-%d %H:%M} {self.status}'


class ScheduledJobChange(BaseModel):
    """Every flip of the switch. Append-only — the UI cannot delete these."""
    job        = models.ForeignKey(ScheduledJob, on_delete=models.CASCADE,
                                   related_name='changes')
    changed_by = models.CharField(max_length=120, blank=True, default='')
    turned_on  = models.BooleanField()
    reason     = models.CharField(max_length=200, blank=True, default='')
    at_ip      = models.CharField(max_length=64, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.job_id} -> {"on" if self.turned_on else "off"}'
