"""
hris/roster_flag_models.py — "this person isn't mine" (CFO 2026-07-26).

Kago's test found Milidzani Muzila on his roster when she is no longer his, and
Shane Thabo Khupe had left the company entirely. Both were only discovered because
someone happened to look. This gives every manager a one-click way to say so from
their own roster.

Deliberately a FLAG, not a direct edit. A manager may raise it; only HR acts on it.
Reasons:
  * moving a reporting line changes leave approval routing — that is HR's call;
  * marking someone as resigned touches payroll and is a CFO-authorised action;
  * a wrong click must never silently rewrite the org chart or end someone's job.
So the manager's click is evidence, and HR's action is the change.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel


class FlagKind(models.TextChoices):
    NOT_MY_REPORT = 'not_mine',  'Not reporting to me'
    RESIGNED      = 'resigned',  'Resigned / left the company'
    ON_LEAVE_LONG = 'long_leave', 'On long leave — exclude this month'
    WRONG_DETAILS = 'wrong_info', 'Job title or details are wrong'
    OTHER         = 'other',     'Something else'


class FlagStatus(models.TextChoices):
    OPEN     = 'open',     'Open — waiting for HR'
    ACTIONED = 'actioned', 'Actioned by HR'
    REJECTED = 'rejected', 'Not accepted'


class RosterFlag(AuditableMixin, BaseModel):
    """A manager flagging something wrong about someone on their roster."""

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE, related_name='roster_flags')
    raised_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='roster_flags_raised')
    kind = models.CharField(max_length=12, choices=FlagKind.choices)
    note = models.TextField(blank=True, default='')

    status = models.CharField(max_length=10, choices=FlagStatus.choices,
                              default=FlagStatus.OPEN)
    actioned_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='roster_flags_actioned')
    actioned_at = models.DateTimeField(null=True, blank=True)
    hr_note = models.TextField(blank=True, default='')

    # CFO correction 2026-07-26: a flagged person does NOT disappear from the
    # roster. They stay visible, marked "waiting on Unami", and Unami makes the
    # decision. Letting a manager's own click remove someone from their roster
    # would let anyone drop an inconvenient person out of their accountability
    # without a decision ever being made.
    DECIDED_BY_EMAIL = 'ubutale@alphadirect.co.bw'      # Chief Human Capital Officer
    # CFO 2026-09-05: Dorothy picks these up too ("Doroti, the HR manager, can
    # then pick it up and assign the relevant team members"). Unami stays first.
    DECIDER_EMAILS = ('ubutale@alphadirect.co.bw',        # Unami Butale, CHCO
                      'dikgopoleng@alphadirect.co.bw')    # Dorothy Ikgopoleng, HRBP
    WAITING_ON = 'HR (Unami or Dorothy)'

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'kind'], name='hris_rflag_status_idx'),
            models.Index(fields=['profile', 'status'], name='hris_rflag_prof_idx'),
        ]
        # One OPEN flag of a kind per person per raiser — clicking twice is not two
        # problems. Enforced in the service via get_or_create.
        verbose_name = 'Roster flag'
        verbose_name_plural = 'Roster flags'

    def __str__(self):
        return f'{self.profile.employee.full_name} — {self.kind} ({self.status})'

    def clean(self):
        super().clean()
        if self.kind == FlagKind.OTHER and not (self.note or '').strip():
            raise ValidationError({'note': 'Say what is wrong.'})
        if self.kind == FlagKind.RESIGNED and not (self.note or '').strip():
            raise ValidationError(
                {'note': 'For a resignation, say when they left or what you know — '
                         'HR needs it to action payroll.'})


def open_flags_for(raiser_user, profile_ids=None) -> dict:
    """{profile_id: {kind, kind_label, note, raised_at}} for this manager's OPEN
    flags — so the roster can show "flagged, waiting on Unami" against the row.

    The person STAYS on the roster (CFO 2026-07-26). The flag is a marker plus a
    decision request, never a way to make someone vanish.
    """
    if raiser_user is None or not getattr(raiser_user, 'is_authenticated', False):
        return {}
    qs = RosterFlag.objects.filter(raised_by=raiser_user, status=FlagStatus.OPEN)
    if profile_ids is not None:
        qs = qs.filter(profile_id__in=list(profile_ids))
    return {
        str(f.profile_id): {
            'flag_id': str(f.id),
            'kind': f.kind,
            'kind_label': f.get_kind_display(),
            'note': f.note,
            'raised_at': f.created_at,
            'waiting_on': RosterFlag.WAITING_ON,
        }
        for f in qs
    }


def decider_user():
    """Who decides a roster flag: Unami (Chief Human Capital Officer), falling
    back to the CFO then any superuser so a flag is never orphaned."""
    from django.contrib.auth.models import User
    for email in (*RosterFlag.DECIDER_EMAILS, 'pganesharajah@alphadirect.co.bw'):
        u = User.objects.filter(email__iexact=email, is_active=True).first()
        if u:
            return u
    return User.objects.filter(is_superuser=True).order_by('id').first()


def can_decide(user) -> bool:
    """Unami or Dorothy decides. The CFO and superusers can too, so nothing gets stuck."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    return email in {*(e.lower() for e in RosterFlag.DECIDER_EMAILS),
                     'pganesharajah@alphadirect.co.bw'}
