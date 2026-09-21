"""
hris/late_notice_models.py — "I'm running late this morning" (CFO 2026-09-09).

Rule 1b of the performance review engine. First activity after 08:15 costs the
month two points; the CFO will forgive it if the person SAYS SO — but only if
they say so before they could possibly know they had been caught:

  "i can forgive the 8.15 am rule if they notify in omni why they are late
   or sick leave or planned leave"

Two hard limits, both deliberate:

  * TODAY ONLY. A notice cannot be filed for a past day. Without this, the
    first thing anyone does on the 10th is back-fill an excuse for every late
    morning in the month and rule 1 becomes a button-pressing exercise.
  * BEFORE 09:00 Botswana (CFO 2026-09-09, tightened from the 10:00 first
    proposed). 08:15 is the line; a notice filed at 11:00 is not a warning,
    it is a defence.

The cap lives in the scorer, not here: the first three late days of a month are
forgiven, and from the fourth the day counts even when notified — so the record
of every notice must survive regardless of whether it earned forgiveness.
"""
from __future__ import annotations

import datetime

from django.db import models

from core.models import BaseModel

# Botswana is UTC+2 year-round; the box runs UTC.
LOCAL_OFFSET = datetime.timedelta(hours=2)
# A notice filed after this local time does not excuse the morning.
NOTICE_CUTOFF = datetime.time(9, 0)


def local_now(now=None):
    """`now` (UTC, tz-aware) as Botswana wall-clock."""
    from django.utils import timezone
    return (now or timezone.now()) + LOCAL_OFFSET


class LateNotice(BaseModel):
    """One person telling Omni, on the morning itself, that they are late."""

    class Kind(models.TextChoices):
        LATE       = 'late',       'Running late'
        SICK       = 'sick',       'Sick today'
        CLIENT     = 'client',     'With a client first'
        OTHER      = 'other',      'Other'

    profile     = models.ForeignKey(
                      'hris.HRISProfile', on_delete=models.CASCADE,
                      related_name='late_notices',
                  )
    notice_date = models.DateField(
                      db_index=True,
                      help_text='The morning this excuses. Always the day it was filed.',
                  )
    kind        = models.CharField(max_length=8, choices=Kind.choices, default=Kind.LATE)
    reason      = models.TextField(
                      blank=True, default='',
                      help_text='In their own words. Shown to the line manager and '
                                'listed on the monthly review.',
                  )
    # Stamped at save so a later change to the cutoff cannot retrospectively
    # turn an accepted notice into a rejected one.
    filed_local_time = models.TimeField(
                           null=True, blank=True,
                           help_text='Botswana wall-clock time the notice was filed.',
                       )
    in_time     = models.BooleanField(
                      default=True,
                      help_text='Filed before the 09:00 cutoff. A late notice is kept '
                                'on the record but never forgives the morning.',
                  )

    class Meta(BaseModel.Meta):
        ordering            = ['-notice_date']
        unique_together     = [('profile', 'notice_date')]
        verbose_name        = 'Late Notice'
        verbose_name_plural = 'Late Notices'

    def __str__(self):
        return f'{self.profile_id} — {self.notice_date} ({self.kind})'

    @classmethod
    def forgiving_dates(cls, profile_ids, start, end) -> dict:
        """{profile_id: {date, ...}} of mornings an IN-TIME notice excuses.

        Out-of-time notices are deliberately excluded here rather than filtered
        by the caller — every caller would have to remember, and one that forgot
        would hand out forgiveness for an excuse filed at lunchtime.
        """
        out: dict = {}
        rows = cls.objects.filter(profile_id__in=list(profile_ids),
                                  notice_date__gte=start, notice_date__lte=end,
                                  in_time=True).values_list('profile_id', 'notice_date')
        for pid, d in rows:
            out.setdefault(pid, set()).add(d)
        return out
