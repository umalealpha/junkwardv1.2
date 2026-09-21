"""
hris/disciplinary_models.py

Staff disciplinary cases. A people-manager raises a case about an employee;
HR (Unami) reviews it; the CFO signs off on suspension / dismissal
(Alpha Direct policy — terminations require CFO authorisation). Mirrors the
leave-encashment module's status-driven chain (hris/leave_encash_models.py).

SENSITIVE HR DATA. Visibility is limited in disciplinary_views to HR, the CFO,
and the manager who raised the case. Every write is audit-logged via
AuditableMixin. CFO directive 2026-07-22 (build the tool so Bharath can run
disciplinary himself).

NATURAL JUSTICE (CFO directive 2026-08-11). A warning issued without a recorded
invitation-to-respond and a recorded response is challengeable. So the chain now
carries an INQUIRY stage: the employee is emailed the allegation and a response
deadline, and HR cannot issue until either the employee's explanation is on the
record or the deadline has passed. The employee's own narrow view of their case
lives in hris/feature_views.my_disciplinary — it is scoped by employee id and
never widens the HR-internal board.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel

# Default days the employee gets to answer. The issuer may override per case;
# the floor stops a one-hour "deadline" being used to fake the step.
DEFAULT_RESPONSE_DAYS = 5
MIN_RESPONSE_DAYS = 2


class DisciplinaryCase(AuditableMixin, BaseModel):
    class Category(models.TextChoices):
        VERBAL     = 'verbal',     'Verbal warning'
        WRITTEN    = 'written',    'Written warning'
        FINAL      = 'final',      'Final written warning'
        SUSPENSION = 'suspension', 'Suspension'
        DISMISSAL  = 'dismissal',  'Dismissal recommendation'

    # Serious sanctions need CFO sign-off before they can be issued.
    CFO_REQUIRED = (Category.SUSPENSION, Category.DISMISSAL)

    class Status(models.TextChoices):
        PENDING_HR       = 'pending_hr',       'Awaiting HR review'
        PENDING_RESPONSE = 'pending_response', 'Awaiting employee response'
        PENDING_CFO      = 'pending_cfo',      'Awaiting CFO sign-off'
        ISSUED           = 'issued',           'Issued'
        REJECTED         = 'rejected',         'Rejected'

    OPEN_STATUSES = (Status.PENDING_HR, Status.PENDING_RESPONSE, Status.PENDING_CFO)

    subject_employee = models.ForeignKey('payroll.Employee', on_delete=models.PROTECT,
                                         related_name='disciplinary_cases')
    subject_name = models.CharField(max_length=191, blank=True, default='',
                                    help_text='Snapshot of the employee name at raise time.')

    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='disciplinary_raised')
    raised_by_email = models.CharField(max_length=254, blank=True, default='')

    category      = models.CharField(max_length=16, choices=Category.choices, db_index=True)
    incident_date = models.DateField(help_text='When the incident occurred.')
    allegation    = models.TextField(help_text='Factual description of the misconduct (minimum 50 words).')
    proposed_action = models.TextField(blank=True, default='',
                                       help_text='What the manager proposes (e.g. written warning on file).')

    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.PENDING_HR, db_index=True)

    hr_reviewer     = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name='+')
    hr_reviewed_at  = models.DateTimeField(null=True, blank=True)
    cfo_approver    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name='+')
    cfo_approved_at = models.DateTimeField(null=True, blank=True)
    issued_at       = models.DateTimeField(null=True, blank=True)

    # ── Natural justice: invitation to respond + the employee's answer ───────
    # inquiry_issued_at is the PROOF the employee was told the allegation and
    # given a deadline. employee_response is their own words, stored on the case
    # record rather than left in a mailbox.
    inquiry_issued_at = models.DateTimeField(null=True, blank=True,
                                            help_text='When the inquiry letter was emailed to the employee.')
    inquiry_issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')
    inquiry_sent_to   = models.CharField(max_length=254, blank=True, default='',
                                         help_text='Address the inquiry letter was sent to (evidence of service).')
    response_deadline = models.DateField(null=True, blank=True,
                                         help_text='Date by which the employee must answer.')

    employee_response      = models.TextField(blank=True, default='',
                                              help_text="The employee's own explanation, in their words.")
    employee_responded_at  = models.DateTimeField(null=True, blank=True)
    # NULL means the employee typed it themselves in omni. Set means HR captured
    # a reply that arrived outside the system (emailed, or a signed letter). The
    # difference is deliberately visible: a response HR typed is weaker evidence
    # than one the employee submitted under their own login, and a reader of the
    # record must be able to tell which they are looking at.
    response_recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name='+',
                                             help_text='Set only when HR captured a reply received '
                                                       'outside omni, on the employee behalf.')

    rejected_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='+')
    rejected_at    = models.DateTimeField(null=True, blank=True)
    rejected_stage = models.CharField(max_length=8, blank=True, default='')
    decision_notes = models.TextField(blank=True, default='')

    # A rejection is normally final. The CFO may overturn one (CFO 2026-08-12,
    # Pako Kago written warning: HR rejected on process — "must be raised through
    # the manager" — not on the facts). The reason lives in its OWN field so the
    # rejecter's decision_notes survive intact: the record must keep showing that
    # HR rejected it and why, alongside who overturned it and why.
    override_reason = models.TextField(blank=True, default='')

    # Set when a case was issued WITHOUT the employee having been heard — the CFO
    # forcing past the natural-justice gate (CFO decision 2026-08-12). It is a
    # permanent stamp on the file, not a workflow flag: the whole value of the
    # gate is that walking around it leaves a mark. Its reason is separate from
    # override_reason so overturning a rejection and skipping the hearing are
    # never conflated — a case can be one, the other, or both.
    unheard_issue_reason = models.TextField(
        blank=True, default='',
        help_text='Reason given for issuing without the employee being heard. '
                  'Set only when the natural-justice gate was forced.')
    unheard_issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Disciplinary Case'
        verbose_name_plural = 'Disciplinary Cases'

    def __str__(self) -> str:
        return f'{self.get_category_display()} — {self.subject_name or self.subject_employee_id}'

    @property
    def needs_cfo(self) -> bool:
        return self.category in self.CFO_REQUIRED

    @property
    def default_deadline(self):
        return (timezone.localdate() + timedelta(days=DEFAULT_RESPONSE_DAYS))

    @property
    def deadline_passed(self) -> bool:
        """True once the response window has closed. False when no deadline is
        set — an absent deadline must never read as 'window closed', or the
        natural-justice gate would pass on a case that was never served."""
        if not self.response_deadline:
            return False
        return timezone.localdate() > self.response_deadline

    @property
    def has_response(self) -> bool:
        return bool(self.employee_responded_at)

    @property
    def response_is_self_submitted(self) -> bool:
        """True only when the employee wrote it themselves under their own login."""
        return bool(self.employee_responded_at) and self.response_recorded_by_id is None

    @property
    def response_arrived_after_decision(self) -> bool:
        """The employee answered only after the outcome was already recorded.

        A true statement about the sequence, kept as a property rather than a
        stored flag so it can never disagree with the timestamps it describes.
        """
        if not (self.employee_responded_at and self.issued_at):
            return False
        return self.employee_responded_at > self.issued_at

    @property
    def issued_without_being_heard(self) -> bool:
        """True when this outcome was recorded without the employee's side.

        Derived, so it cannot be set to a comfortable value independently of the
        facts: a case is 'unheard' if it was issued and natural justice was never
        satisfied on it. Anything reading the file — the board, an audit, a
        challenge — gets the same answer.
        """
        return bool(self.issued_at) and not self.natural_justice_satisfied

    @property
    def natural_justice_satisfied(self) -> bool:
        """The gate on issuing. Requires a POSITIVE record of service (the
        inquiry was actually sent) AND either the employee's answer or an
        expired deadline. Never satisfied by a default or a missing value."""
        if not self.inquiry_issued_at:
            return False
        return self.has_response or self.deadline_passed


class DisciplinaryAttachment(AuditableMixin, BaseModel):
    """Supporting evidence a manager attaches to a disciplinary case — email
    exports, WhatsApp screenshots, PDFs, Word docs, images.

    Files live in media/disciplinary/YYYY/MM/ and are NEVER served straight off
    MEDIA_URL (evidence may contain personal data). They are streamed only
    through the gated download view, which enforces the SAME visibility gate as
    the parent case (raiser / HR / CFO). No external processing. Every write is
    audit-logged via AuditableMixin.
    """

    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE,
                             related_name='attachments')
    file = models.FileField(upload_to='disciplinary/%Y/%m/')
    filename     = models.CharField(max_length=255, blank=True, default='',
                                    help_text='Original upload filename.')
    content_type = models.CharField(max_length=120, blank=True, default='',
                                    help_text='Browser-reported MIME type at upload.')
    size         = models.PositiveIntegerField(default=0, help_text='File size in bytes.')
    uploaded_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='disciplinary_attachments')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Disciplinary Attachment'
        verbose_name_plural = 'Disciplinary Attachments'

    def __str__(self) -> str:
        return f'{self.filename or (self.file.name if self.file else "")} — case {self.case_id}'
