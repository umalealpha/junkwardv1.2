"""What the gate is doing, right now (CFO 2026-09-09).

He runs several chats at once, each building a different part of omni, and the
only way to see what was happening was four browser tabs on GitHub. This is the
one board: every branch building now, which machine and which chat started it,
and how long is left.

  CiRun   one gate run — a branch, a commit, a push or a pull request.
  CiJob   one runner inside it: the four shards, the migrations proof, the
          summary. Its duration is what makes a countdown possible.

WHERE THE DATA COMES FROM. GitHub pushes it here as it happens. omni holds no
GitHub credential and asks GitHub nothing — a webhook means live updates with
no token to store, rotate or leak.

WHY DURATIONS ARE KEPT. "Running" is not an answer to "how long". Measured
2026-09-09 after the gate was rebuilt: shard 1 8m50, shard 2 6m22, shard 3 5m09,
shard 4 6m20. Those came from reading logs by hand. Keeping every job's real
duration means the estimate improves by itself and never goes stale.
"""
from __future__ import annotations

import datetime as dt

from django.db import models
from django.utils import timezone

from core.models import BaseModel


class CiRun(BaseModel):
    """One gate run."""

    class Status(models.TextChoices):
        QUEUED      = 'queued',      'Queued'
        RUNNING     = 'running',     'Running'
        SUCCESS     = 'success',     'Passed'
        FAILURE     = 'failure',     'Failed'
        CANCELLED   = 'cancelled',   'Cancelled'

    run_id      = models.BigIntegerField(unique=True, db_index=True)
    run_number  = models.IntegerField(default=0)
    workflow    = models.CharField(max_length=120, blank=True, default='')
    branch      = models.CharField(max_length=200, db_index=True)
    head_sha    = models.CharField(max_length=40, blank=True, default='')
    title       = models.CharField(max_length=300, blank=True, default='')
    actor       = models.CharField(max_length=120, blank=True, default='')
    event       = models.CharField(max_length=40, blank=True, default='')
    url         = models.URLField(blank=True, default='')

    status      = models.CharField(max_length=12, choices=Status.choices,
                                   default=Status.QUEUED, db_index=True)
    started_at  = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_at    = models.DateTimeField(null=True, blank=True)

    # Which seat pushed it. Both machines report the hostname "Prat", so this is
    # only ever set from what the pushing machine tells the build log, never
    # guessed from a name.
    machine     = models.CharField(max_length=16, blank=True, default='')

    class Meta:
        ordering = ['-started_at', '-run_id']

    def __str__(self):
        return f'{self.branch} #{self.run_number} {self.status}'

    @property
    def is_live(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)

    def elapsed_seconds(self) -> int:
        if not self.started_at:
            return 0
        end = self.ended_at or timezone.now()
        return max(int((end - self.started_at).total_seconds()), 0)


class CiJob(BaseModel):
    """One runner inside a run. The unit a countdown can actually be built on."""

    run         = models.ForeignKey(CiRun, related_name='jobs', on_delete=models.CASCADE)
    job_id      = models.BigIntegerField(unique=True, db_index=True)
    name        = models.CharField(max_length=120, db_index=True)
    status      = models.CharField(max_length=12, choices=CiRun.Status.choices,
                                   default=CiRun.Status.QUEUED, db_index=True)
    started_at  = models.DateTimeField(null=True, blank=True)
    ended_at    = models.DateTimeField(null=True, blank=True)
    url         = models.URLField(blank=True, default='')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} {self.status}'

    def elapsed_seconds(self) -> int:
        if not self.started_at:
            return 0
        end = self.ended_at or timezone.now()
        return max(int((end - self.started_at).total_seconds()), 0)

    def duration_seconds(self) -> int | None:
        """Only a FINISHED job has a duration. A running one has an elapsed
        time, which is a different thing — mixing them is how an estimate
        quietly becomes a lie about work still in progress."""
        if self.started_at and self.ended_at:
            return max(int((self.ended_at - self.started_at).total_seconds()), 0)
        return None


def typical_seconds(job_name: str, *, sample: int = 12) -> int | None:
    """How long this job usually takes, from its own recent history.

    The MEDIAN, not the mean: one 40-minute run that stalled on a queued runner
    would drag an average up and make every countdown wrong for days. Median
    ignores it.

    Successes only. A job that failed in 90 seconds finished early precisely
    because it did NOT do the work, and letting those in would make the gate
    look faster the more it broke.

    None when there is not enough history — the board then says "running" and
    no number, which is honest. A made-up countdown is worse than none.
    """
    rows = (CiJob.objects
            .filter(name=job_name, status=CiRun.Status.SUCCESS,
                    started_at__isnull=False, ended_at__isnull=False)
            .order_by('-ended_at')[:sample])
    durations = sorted(d for d in (j.duration_seconds() for j in rows) if d)
    if len(durations) < 3:
        return None
    mid = len(durations) // 2
    if len(durations) % 2:
        return durations[mid]
    return (durations[mid - 1] + durations[mid]) // 2


def eta_seconds(job: CiJob) -> int | None:
    """Seconds left on a running job, or None if we cannot say honestly."""
    if job.status != CiRun.Status.RUNNING or not job.started_at:
        return None
    typical = typical_seconds(job.name)
    if typical is None:
        return None
    return max(typical - job.elapsed_seconds(), 0)


def local_now() -> dt.datetime:
    return timezone.localtime()
