"""
hris/talent_cockpit_models.py — Development Dialogue "Talent Cockpit".

CFO directive 2026-07-17: omni becomes the performance-evaluation system of
record. No more lost Excel files — the Development Dialogue (the PMS "Alpha
Direct" sheet: competency SBI narratives, manager/employee/weighted scores,
value behaviours, PDP, career aspirations, development priorities, and the
9-box placement) lives here, editable, server-saved, audited, and gated to
the HR/CFO whitelist.

One row per person. The full structured dialogue is kept in `payload`
(JSON — matches omni's competency/OKR JSON convention, no child tables), with
the headline fields denormalised onto columns for listing/search/reporting.
"""
from __future__ import annotations

from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from core.models import AuditableMixin, BaseModel


class DevelopmentDialogue(AuditableMixin, BaseModel):
    """One person's editable Development Dialogue / performance evaluation."""

    # Stable reconciliation key = the cockpit app's person id (int or ts),
    # stored as text so add/remove in the UI round-trips cleanly. One ref per
    # person PER PERIOD (a new period gets a new ref).
    ref        = models.CharField(max_length=80, unique=True, db_index=True)

    # person_key groups all of one person's periods together (stable across
    # periods); is_current marks the live period. Archiving a period (starting
    # a new one) keeps the old row for the record with is_current=False.
    person_key = models.CharField(max_length=80, blank=True, default='', db_index=True)
    is_current = models.BooleanField(default=True, db_index=True)
    # Once the review is signed off it locks — no further edits to that period.
    locked     = models.BooleanField(default=False)

    name       = models.CharField(max_length=200)
    # Owner's work email — lets a staff member open ONLY their own dialogue
    # (self-service), without any access to colleagues' records or payroll.
    email      = models.CharField(max_length=200, blank=True, default='', db_index=True)
    department = models.CharField(max_length=200, blank=True, default='')
    position   = models.CharField(max_length=200, blank=True, default='')
    period     = models.CharField(max_length=120, blank=True, default='')
    supervisor = models.CharField(max_length=200, blank=True, default='')
    color      = models.CharField(max_length=9,   blank=True, default='')

    # 9-box placement (0..1). Kept as columns so a future SQL/board report can
    # read them without unpacking JSON.
    performance = models.FloatField(default=0.5)
    potential   = models.FloatField(default=0.5)
    overall     = models.FloatField(null=True, blank=True,
                     help_text='Overall Score (All Sections) as a percentage, 0-100.')
    rating      = models.CharField(max_length=120, blank=True, default='')

    # The full cockpit person object (details, competency sections + rows with
    # SBI/manager/employee/weight/weighted/comments, value behaviours, PDP,
    # career aspirations, development priorities/measures, targets, job desc).
    payload    = models.JSONField(default=dict, blank=True)

    # Optional link to the payroll record (HR can attach later; not required so
    # the evaluation never depends on messy/duplicate employee rows).
    employee   = models.ForeignKey(
                     'payroll.Employee', null=True, blank=True,
                     on_delete=models.SET_NULL, related_name='development_dialogues',
                 )

    class Meta(BaseModel.Meta):
        ordering            = ['name']
        verbose_name        = 'Development Dialogue'
        verbose_name_plural = 'Development Dialogues'

    def __str__(self):
        return f"{self.name} — Development Dialogue ({self.overall if self.overall is not None else '—'})"

    def delete(self, *args, **kwargs):
        """A signed-off dialogue cannot be deleted. By anyone.

        NOTE the signal below does the real work: Django's queryset `.delete()`
        and cascades NEVER call this method, so a lock enforced only here is one
        ORM one-liner away from being bypassed (Fable, 2026-07-31).

        CFO 2026-07-31, on loading the FY2025 reviews: "Nobody should be able to
        delete it, including me or you — these are for the past periods." A
        completed performance review is a record of what was said to a person
        about their career; losing one is not recoverable from anywhere else, and
        the earlier bulk-save footgun already destroyed 6 real records once.

        Enforced on the model, not in a view, so the admin site, a management
        command and a stray ORM call are all covered. Unlock deliberately requires
        editing `locked` first — an explicit, audited act.
        """
        if self.locked:
            raise PermissionError(
                f'Development Dialogue "{self.name}" ({self.period or "no period"}) is '
                f'signed off and cannot be deleted. Past-period reviews are permanent.')
        return super().delete(*args, **kwargs)


@receiver(pre_delete, sender=DevelopmentDialogue)
def _block_locked_dialogue_delete(sender, instance, **kwargs):
    """The lock that actually holds.

    `Model.delete()` is not called by `QuerySet.delete()`, cascades, or admin bulk
    actions — `pre_delete` is. Without this, one
    `DevelopmentDialogue.objects.filter(...).delete()` silently wipes signed-off
    reviews, which is the same footgun class that destroyed 6 real records once.

    Honest limit, stated to the CFO: this protects every path THROUGH THE
    APPLICATION. Raw SQL or direct database access is only defensible with a DB
    trigger plus the 8-hourly backups.
    """
    if getattr(instance, 'locked', False):
        raise PermissionError(
            f'Development Dialogue "{instance.name}" ({instance.period or "no period"}) is '
            f'signed off and cannot be deleted. Past-period reviews are permanent.')
