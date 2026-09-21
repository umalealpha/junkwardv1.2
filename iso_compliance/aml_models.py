"""
iso_compliance/aml_models.py — the AML/CFT and market-conduct registers a
Botswana insurer's Compliance Officer is legally required to keep.

CFO 2026-09-09, after we established that Kakale Botana (AML/CFT Officer) had
two tasks in her entire Omni history and none of these registers existed:

    "further find out what else a compliance manager / AML officer should do so
     we can include it for her, eg managing risk registers etc for an insurance
     company, ensure board meeting information is send to EXCO 5th of every
     quarter ending"

What each register answers to (checked against NBFIRA's own guidance, not
invented):
  * ComplianceReport            — the quarterly report to the Board/EXCO. The
                                  Board carries overall responsibility for
                                  regulatory compliance and delegates to a
                                  designated Compliance Officer; this is the
                                  paper that discharges it. CFO's rule: due the
                                  5th of the month after each quarter end.
  * SanctionsScreening          — the Financial Intelligence Act duty to search
                                  the database against the UNSC lists and report
                                  a positive match to the FIA WITHOUT DELAY. The
                                  PEP verdict rides on the same row: NBFIRA's
                                  guidance treats identification of prominent
                                  and influential persons as part of the same
                                  customer-identification exercise, so splitting
                                  it into a second register would mean screening
                                  the same person twice and reconciling two
                                  answers.
  * SuspiciousTransactionReport — what gets filed with the Financial
                                  Intelligence Agency once something is
                                  suspicious.
  * RegulatoryBreach            — a breach of a rule or licence condition. NOT
                                  core.BreachIncident, which is the DATA-breach
                                  register on the 72-hour IDPC clock and belongs
                                  to the Data Protection Officer. A late NBFIRA
                                  return and a leaked customer list are
                                  different incidents with different regulators,
                                  different clocks and different owners.
  * AMLTrainingRecord           — the staff training programme NBFIRA requires,
                                  with evidence of who actually sat it.
  * CustomerComplaint           — the market-conduct complaints register.

The enterprise risk register is NOT here: iso_compliance.Risk already exists
(ISO 27005 shape, likelihood x impact, owner, treatment, reviewed_at). It has
zero rows, which is a filling problem, not a modelling one.

NOTHING here moves money and nothing here stores an Omang or a bank account.
A screening row carries the name that was screened and the verdict, which is the
minimum the Act requires to evidence that the search happened.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel

# The CFO's rule: the quarter's pack is due on the 5th of the following month.
BOARD_PACK_DUE_DAY = 5
# How long a customer complaint may sit before it counts as overdue.
COMPLAINT_SLA_DAYS = 30
# AML training is an annual refresher.
TRAINING_VALID_DAYS = 365


def quarter_of(day: dt.date) -> tuple[int, int]:
    """(year, quarter 1-4) for a calendar date."""
    return day.year, (day.month - 1) // 3 + 1


def quarter_end(year: int, quarter: int) -> dt.date:
    """Last day of the calendar quarter."""
    last_month = quarter * 3
    if last_month == 12:
        return dt.date(year, 12, 31)
    return dt.date(year, last_month + 1, 1) - dt.timedelta(days=1)


def board_pack_due(year: int, quarter: int) -> dt.date:
    """The 5th of the month AFTER the quarter ends (CFO 2026-09-09)."""
    end = quarter_end(year, quarter)
    nxt = end + dt.timedelta(days=1)
    return nxt.replace(day=BOARD_PACK_DUE_DAY)


class ComplianceReport(AuditableMixin, BaseModel):
    """The quarterly compliance report to the Board / EXCO."""

    class Status(models.TextChoices):
        DRAFT  = 'draft',  'Draft'
        SENT   = 'sent',   'Sent to EXCO'
        NOTED  = 'noted',  'Noted by the Board'

    class Kind(models.TextChoices):
        """Which officer's quarterly report this is.

        Added 2026-09-09 when the Data Protection Officer got the same
        obligation as the AML/CFT Officer. Without it the two would collide on
        (year, quarter) and one officer filing would silently discharge the
        other's duty — the AML pack closing the DPO's task is exactly the kind
        of false 'done' this module exists to stop.
        """
        AML        = 'aml',        'AML/CFT compliance'
        DATA_PROT  = 'data_prot',  'Data protection (DPO)'

    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.AML,
                            db_index=True)
    period_year    = models.PositiveSmallIntegerField()
    period_quarter = models.PositiveSmallIntegerField(help_text='1-4, calendar quarter.')
    title          = models.CharField(max_length=200, blank=True, default='')
    summary        = models.TextField(blank=True, default='')
    document       = models.FileField(
        upload_to='compliance/board-packs/', null=True, blank=True,
        help_text='The pack itself. A report with no document is not a report.')
    status      = models.CharField(max_length=10, choices=Status.choices,
                                   default=Status.DRAFT)
    sent_at     = models.DateTimeField(null=True, blank=True)
    recipients  = models.TextField(
        blank=True, default='',
        help_text='Who it went to, one address per line.')
    prepared_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-period_year', '-period_quarter']
        unique_together = [('kind', 'period_year', 'period_quarter')]
        verbose_name = 'Compliance Report to EXCO'
        verbose_name_plural = 'Compliance Reports to EXCO'

    @property
    def due_on(self) -> dt.date:
        return board_pack_due(self.period_year, self.period_quarter)

    @property
    def is_discharged(self) -> bool:
        """Filed for real: sent AND with a document attached.

        Deliberately strict. A status flipped to 'sent' with nothing attached is
        exactly the kind of self-declared completion this whole module exists to
        stop — the objective it closes is a regulatory duty, not a to-do.
        """
        return bool(self.document) and self.status in (self.Status.SENT, self.Status.NOTED)

    def __str__(self):
        return (f'{self.get_kind_display()} report {self.period_year} '
                f'Q{self.period_quarter} ({self.status})')


class SanctionsScreening(AuditableMixin, BaseModel):
    """One screening of one party against the sanctions lists, plus the PEP verdict."""

    class SubjectType(models.TextChoices):
        CUSTOMER = 'customer', 'Customer / policyholder'
        SUPPLIER = 'supplier', 'Supplier / vendor'
        CLAIMANT = 'claimant', 'Claimant / third party'
        STAFF    = 'staff',    'Employee'
        OTHER    = 'other',    'Other counterparty'

    class Result(models.TextChoices):
        CLEAR    = 'clear',    'Clear — no match'
        POSSIBLE = 'possible', 'Possible match — under review'
        MATCH    = 'match',    'Confirmed match'

    class PEP(models.TextChoices):
        NONE      = 'none',      'Not a PEP'
        PEP       = 'pep',       'Politically Exposed Person'
        ASSOCIATE = 'associate', 'Close associate / family of a PEP'
        UNCHECKED = 'unchecked', 'Not yet assessed'

    subject_type = models.CharField(max_length=10, choices=SubjectType.choices,
                                    default=SubjectType.CUSTOMER)
    subject_name = models.CharField(max_length=200)
    subject_ref  = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Policy number, supplier code or claim number — NOT an Omang.')
    list_source  = models.CharField(
        max_length=100, default='UNSC',
        help_text='Which list was searched, e.g. UNSC Consolidated.')
    list_version = models.CharField(
        max_length=60, blank=True, default='',
        help_text='List date/version searched — what makes the search repeatable.')
    result       = models.CharField(max_length=10, choices=Result.choices,
                                    default=Result.CLEAR)
    pep_status   = models.CharField(max_length=10, choices=PEP.choices,
                                    default=PEP.UNCHECKED)
    screened_at  = models.DateTimeField(default=timezone.now, db_index=True)
    screened_by  = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # The Act's "without delay" duty — a confirmed match with this empty is the
    # single most serious open item this module can hold.
    reported_to_fia_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-screened_at']
        verbose_name = 'Sanctions / PEP Screening'
        verbose_name_plural = 'Sanctions / PEP Screenings'
        indexes = [
            models.Index(fields=['result'], name='sancscr_result_idx'),
            models.Index(fields=['pep_status'], name='sancscr_pep_idx'),
            models.Index(fields=['subject_type', 'screened_at'], name='sancscr_type_when_idx'),
        ]

    @property
    def needs_fia_report(self) -> bool:
        return self.result == self.Result.MATCH and self.reported_to_fia_at is None

    def __str__(self):
        return f'{self.subject_name} — {self.result} ({self.screened_at:%Y-%m-%d})'


class SuspiciousTransactionReport(AuditableMixin, BaseModel):
    """An STR to the Financial Intelligence Agency."""

    class Status(models.TextChoices):
        DETECTED = 'detected', 'Detected — not yet filed'
        FILED    = 'filed',    'Filed with the FIA'
        CLOSED   = 'closed',   'Closed — no report required'

    subject_name = models.CharField(max_length=200)
    subject_ref  = models.CharField(max_length=100, blank=True, default='')
    detected_on  = models.DateField(db_index=True)
    description  = models.TextField(
        help_text='What was suspicious, in plain words.')
    status       = models.CharField(max_length=10, choices=Status.choices,
                                    default=Status.DETECTED)
    filed_at     = models.DateTimeField(null=True, blank=True)
    fia_reference = models.CharField(max_length=100, blank=True, default='')
    screening    = models.ForeignKey(
        SanctionsScreening, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reports')
    raised_by    = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-detected_on']
        verbose_name = 'Suspicious Transaction Report'
        verbose_name_plural = 'Suspicious Transaction Reports'
        indexes = [models.Index(fields=['status'], name='str_status_idx')]

    def __str__(self):
        return f'STR {self.subject_name} ({self.status})'


class RegulatoryBreach(AuditableMixin, BaseModel):
    """A breach of a regulatory rule or licence condition.

    Separate from core.BreachIncident (data breaches, IDPC, 72-hour clock).
    """

    class Severity(models.TextChoices):
        LOW = 'low', 'Low'; MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'; CRITICAL = 'critical', 'Critical'

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'
        REMEDIATED = 'remediated', 'Remediated'
        CLOSED    = 'closed',    'Closed'

    regulator     = models.CharField(max_length=60, default='NBFIRA')
    rule          = models.CharField(
        max_length=200, blank=True, default='',
        help_text='The rule, section or licence condition breached.')
    title         = models.CharField(max_length=200)
    description   = models.TextField(blank=True, default='')
    discovered_on = models.DateField(db_index=True)
    severity      = models.CharField(max_length=10, choices=Severity.choices,
                                     default=Severity.MEDIUM)
    status        = models.CharField(max_length=12, choices=Status.choices,
                                     default=Status.OPEN)
    reported_to_regulator_at = models.DateTimeField(null=True, blank=True)
    remediation   = models.TextField(blank=True, default='')
    closed_on     = models.DateField(null=True, blank=True)
    owner         = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-discovered_on']
        verbose_name = 'Regulatory Breach'
        verbose_name_plural = 'Regulatory Breaches'
        indexes = [models.Index(fields=['status', 'severity'], name='regbreach_status_sev_idx')]

    def __str__(self):
        return f'{self.regulator}: {self.title} ({self.status})'


class AMLTrainingRecord(AuditableMixin, BaseModel):
    """Evidence one person actually sat the AML/CFT training."""

    employee = models.ForeignKey(
        'payroll.Employee', on_delete=models.CASCADE, related_name='aml_training')
    course       = models.CharField(max_length=200, default='AML/CFT awareness')
    completed_on = models.DateField(db_index=True)
    score        = models.PositiveSmallIntegerField(null=True, blank=True)
    evidence     = models.FileField(
        upload_to='compliance/aml-training/', null=True, blank=True)
    recorded_by  = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-completed_on']
        verbose_name = 'AML Training Record'
        verbose_name_plural = 'AML Training Records'
        indexes = [models.Index(fields=['employee', 'completed_on'],
                                name='amltrain_emp_when_idx')]

    @property
    def valid_until(self) -> dt.date:
        return self.completed_on + dt.timedelta(days=TRAINING_VALID_DAYS)

    def __str__(self):
        return f'{self.employee_id} — {self.course} ({self.completed_on})'


class CustomerComplaint(AuditableMixin, BaseModel):
    """Market-conduct complaints register."""

    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'; PHONE = 'phone', 'Phone'
        WALK_IN = 'walk_in', 'Walk-in'; SOCIAL = 'social', 'Social media'
        REGULATOR = 'regulator', 'Via the regulator'; OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        INVESTIGATING = 'investigating', 'Investigating'
        RESOLVED = 'resolved', 'Resolved'
        ESCALATED = 'escalated', 'Escalated to the regulator'

    received_on  = models.DateField(db_index=True)
    complainant  = models.CharField(max_length=200)
    policy_ref   = models.CharField(max_length=100, blank=True, default='')
    channel      = models.CharField(max_length=12, choices=Channel.choices,
                                    default=Channel.EMAIL)
    category     = models.CharField(max_length=100, blank=True, default='')
    summary      = models.TextField()
    status       = models.CharField(max_length=14, choices=Status.choices,
                                    default=Status.OPEN)
    resolved_on  = models.DateField(null=True, blank=True)
    outcome      = models.TextField(blank=True, default='')
    owner        = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-received_on']
        verbose_name = 'Customer Complaint'
        verbose_name_plural = 'Customer Complaints'
        indexes = [models.Index(fields=['status', 'received_on'],
                                name='complaint_status_when_idx')]

    @property
    def is_overdue(self) -> bool:
        if self.status in (self.Status.RESOLVED,):
            return False
        return (timezone.localdate() - self.received_on).days > COMPLAINT_SLA_DAYS

    def __str__(self):
        return f'{self.complainant} ({self.received_on}) — {self.status}'


# The AML/CFT Officer is Alpha Direct's, not the group's. Scoping the register
# to ADIC is not a display preference — an officer measured against people she
# has no remit over can never reach the target, and the 49 M365- rows carried
# in the group figure have no login, no company and are not on payroll (they
# are the phantom records the notebook warns about). Counting them made the
# headline 160 when the real population is 77. Filtering on the company both
# scopes the entity AND drops the phantoms, because every phantom has no
# company at all.
AML_COMPANY_CODE = 'ADIC'


def aml_training_outstanding_qs():
    """Active ADIC staff with no VALID AML training — the single definition.

    Added 2026-09-16. This query was briefly written out twice: once for the
    officer's screen and once in hris.objective_counters for her weekly
    objective. They were identical on the day, which is exactly how drift
    starts — the two would part company the first time someone edited one, and
    the officer would then be sent to chase people the scoreboard had already
    cleared. One reader, one answer.

    'Valid' means completed inside the validity window, not merely completed
    once: a lapsed certificate is not evidence of a trained workforce.
    """
    import datetime as dt

    from payroll.models import Employee

    cutoff = timezone.localdate() - dt.timedelta(days=TRAINING_VALID_DAYS)
    trained = set(AMLTrainingRecord.objects
                  .filter(completed_on__gte=cutoff)
                  .values_list('employee_id', flat=True))
    return (Employee.objects
            .filter(status=Employee.Status.ACTIVE,
                    company__code=AML_COMPANY_CODE)
            .exclude(pk__in=trained))
