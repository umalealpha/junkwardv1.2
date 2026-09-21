"""
devlog/models.py — the CFO's build log (CFO 2026-09-09).

"the objective of the first dashboard is to see what i have done for the whole
day and what finished and what to finish".

Three tables, and the split between them is the whole design:

  DevItem    what he ASKED FOR, in his own words, recorded the moment he says
             it. Nothing else in Omni records this today, which is why the
             dashboard could not be built from what already exists.
  DevDeploy  what actually WENT LIVE. Written by the deploy script itself, so
             "finished" can only ever mean "on prod" — never "code written",
             never "tests pass". A dashboard that calls a green build finished
             is the same lie as a status meeting.
  DevCommit  the commits inside a deploy, linked to an item by an explicit
             trailer. Deliberately NO fuzzy matching on words: a wrong link is
             worse than an honest "not linked", because it invents a finished
             piece of work that nobody did.

Capture is automatic (his instruction, 2026-09-09): /goal, /code, /lane-b and
/fabe write here as they run, and /pending reconciles at the end of a batch.
He never fills in a form.

What this cannot know is stated on the page rather than hidden: anything asked
in plain chat outside those commands is invisible to it, so every deployed
commit with no item shows up in its own band. The gaps are the honest part.
"""
from __future__ import annotations

from django.db import models

from core.models import BaseModel


class DevItem(BaseModel):
    """One thing the CFO asked for."""

    class Status(models.TextChoices):
        ASKED    = 'asked',    'Asked for'
        BUILDING = 'building', 'Being built'
        WAITING  = 'waiting',  'Built, waiting to go live'
        LIVE     = 'live',     'Live'
        PARKED   = 'parked',   'Parked'
        DROPPED  = 'dropped',  'Dropped'

    # His words, untouched. The dashboard shows these rather than a tidied
    # summary — a paraphrase is where "that is not what I asked for" starts.
    asked_text = models.TextField()
    title      = models.CharField(max_length=140, blank=True, default='')
    asked_at   = models.DateTimeField(db_index=True)
    status     = models.CharField(max_length=10, choices=Status.choices,
                                  default=Status.ASKED, db_index=True)

    # Which machine picked it up — this Mac or the Windows PC. Both report the
    # hostname "Prat", so it is recorded by OS, never by name.
    machine     = models.CharField(max_length=16, blank=True, default='')
    # Whichever command opened it: goal / code / lane-b / fabe / deploy / chat.
    source      = models.CharField(max_length=24, blank=True, default='')
    session_ref = models.CharField(max_length=64, blank=True, default='')
    area        = models.CharField(max_length=64, blank=True, default='')

    # How we will know it is done — /goal's success criteria, when there are any.
    success_criteria = models.TextField(blank=True, default='')
    notes            = models.TextField(blank=True, default='')

    live_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # When the CFO answered "no, not done yet" to the looks-done check, so the
    # same row is not put back in front of him every day (CFO 13-Sep-2026).
    confirm_declined_at = models.DateTimeField(null=True, blank=True)
    deploy  = models.ForeignKey('devlog.DevDeploy', null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='items')

    # WHO ASKED (CFO 2026-09-09). Not always the CFO — a request can come from
    # any member of staff, and the dashboard is far more useful when it can be
    # read per requester. Stored both ways on purpose: the FK when the person
    # has an Omni login, the plain name when the ask came from someone who does
    # not (or came in by email). Never guessed from a name (f-never-namematch-td).
    requested_by      = models.ForeignKey('auth.User', null=True, blank=True,
                                          on_delete=models.SET_NULL,
                                          related_name='dev_requests')
    requested_by_name = models.CharField(max_length=120, blank=True, default='',
                                         db_index=True)

    # A build item that came out of a reported bug keeps the thread. The bug
    # board already tells the reporter when it is closed; this is the other
    # half — what was actually built about it.
    bug = models.ForeignKey('core.BugReport', null=True, blank=True,
                            on_delete=models.SET_NULL, related_name='dev_items')

    # Set by whoever raises the row, so two machines cannot open the same item
    # twice for one ask.
    client_key = models.CharField(max_length=120, blank=True, default='',
                                  db_index=True)

    class Meta(BaseModel.Meta):
        ordering = ['-asked_at']
        verbose_name = 'Build item'
        verbose_name_plural = 'Build items'
        constraints = [
            models.UniqueConstraint(fields=['client_key'],
                                    condition=~models.Q(client_key=''),
                                    name='uniq_devitem_client_key'),
        ]

    def __str__(self):
        return f'{self.get_status_display()} — {self.title or self.asked_text[:60]}'

    def save(self, *args, **kwargs):
        # ONE source of truth (CFO 13-Sep-2026): a row whose live_at a release
        # stamped is live. A later skill saying "waiting" cannot un-live it —
        # that is how 11 rows came to read "waiting" with a live date.
        if self.live_at and self.status in (self.Status.ASKED, self.Status.BUILDING,
                                            self.Status.WAITING):
            self.status = self.Status.LIVE
            fields = kwargs.get('update_fields')
            if fields is not None and 'status' not in fields:
                kwargs['update_fields'] = [*fields, 'status']
        super().save(*args, **kwargs)

    @property
    def who_asked(self) -> str:
        """A name to show. Prefers the real login over the typed-in name."""
        if self.requested_by_id:
            u = self.requested_by
            full = (u.get_full_name() or '').strip()
            return full or u.username
        return self.requested_by_name or 'not recorded'

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.ASKED, self.Status.BUILDING,
                               self.Status.WAITING)


class DevDeploy(BaseModel):
    """One release that reached prod. Written by the deploy script."""

    sha          = models.CharField(max_length=40, unique=True)
    prev_sha     = models.CharField(max_length=40, blank=True, default='')
    deployed_at  = models.DateTimeField(db_index=True)
    commit_count = models.PositiveIntegerField(default=0)
    ok           = models.BooleanField(default=True)
    note         = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-deployed_at']
        verbose_name = 'Deploy'
        verbose_name_plural = 'Deploys'

    def __str__(self):
        return f'{self.sha[:8]} — {self.deployed_at:%Y-%m-%d %H:%M}'


class DevCommit(BaseModel):
    """A commit inside a deploy. `item` is set only by an explicit trailer."""

    sha     = models.CharField(max_length=40, unique=True)
    author  = models.CharField(max_length=120, blank=True, default='')
    subject = models.CharField(max_length=300, blank=True, default='')
    committed_at = models.DateTimeField(db_index=True)
    deploy  = models.ForeignKey(DevDeploy, on_delete=models.CASCADE,
                                related_name='commits')
    item    = models.ForeignKey(DevItem, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='commits')

    class Meta(BaseModel.Meta):
        ordering = ['-committed_at']
        verbose_name = 'Commit'
        verbose_name_plural = 'Commits'

    def __str__(self):
        return f'{self.sha[:8]} {self.subject[:60]}'
