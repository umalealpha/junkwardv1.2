"""Staff lifecycle records — HR batch 2 (CFO 19-Sep-2026: "we are building everything today").

HC / Unami 19-Sep-2026:
  * Offboarding must be finished BEFORE Omni archives anyone: HC uploads the resignation
    letter, IT uploads its offboarding document, HC uploads its offboarding document, and
    HC + manager/supervisor + Finance sign off.
  * C-suite, senior managers and senior associates lose system access (not email) as soon
    as HR records the exit; other staff keep it to the end of notice.
  * New joiners: offer letter from the approved Authority to Recruit, JD signed by manager
    and employee, all policies signed within 30 days, monthly review signed by both, a
    30-day checklist, and IT told the systems for the role.

Imported into hris/models.py so Django's app registry sees the models.
"""
from django.conf import settings
from django.db import models

from core.models import AuditableMixin, BaseModel


class OffboardingCase(AuditableMixin, BaseModel):
    """One leaver. Omni refuses to archive the employee until this is complete."""

    class Reason(models.TextChoices):
        RESIGNATION    = 'resignation',    'Resignation'
        END_OF_CONTRACT = 'end_of_contract', 'End of contract'
        DISMISSAL      = 'dismissal',      'Dismissal'
        OTHER          = 'other',          'Other'

    class Status(models.TextChoices):
        OPEN      = 'open',      'In progress'
        COMPLETE  = 'complete',  'Complete'
        CANCELLED = 'cancelled', 'Cancelled'

    class Seniority(models.TextChoices):
        C_SUITE          = 'c_suite',          'C-suite'
        SENIOR_MANAGER   = 'senior_manager',   'Senior manager'
        SENIOR_ASSOCIATE = 'senior_associate', 'Senior associate'
        EMPLOYEE         = 'employee',         'Employee'

    employee = models.ForeignKey('payroll.Employee', on_delete=models.PROTECT,
                                 related_name='offboarding_cases')
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.RESIGNATION)
    last_working_day = models.DateField()
    seniority = models.CharField(max_length=20, choices=Seniority.choices,
                                 default=Seniority.EMPLOYEE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='offboarding_opened')
    access_removed_at = models.DateTimeField(null=True, blank=True,
                                             help_text='When Omni access was closed for this leaver.')
    it_ticket_sent_at = models.DateTimeField(null=True, blank=True)
    m365_disabled_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When Omni switched off the Microsoft 365 account (day after the last working day).')
    m365_note = models.CharField(max_length=300, blank=True, default='',
                                 help_text='Last result of the Microsoft switch-off attempt.')
    completed_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']

    def __str__(self):
        return f'Offboarding {self.employee_id} ({self.status})'


class OffboardingStep(BaseModel):
    """A required upload or sign-off on an offboarding case."""

    class Kind(models.TextChoices):
        RESIGNATION_LETTER = 'resignation_letter', 'Resignation letter (HC uploads)'
        IT_DOCUMENT        = 'it_document',        'IT offboarding document (IT uploads)'
        HC_DOCUMENT        = 'hc_document',        'HC offboarding document (HC uploads)'
        SIGNOFF_HC         = 'signoff_hc',         'Sign-off: Human Capital'
        SIGNOFF_MANAGER    = 'signoff_manager',    'Sign-off: manager / supervisor'
        SIGNOFF_FINANCE    = 'signoff_finance',    'Sign-off: Finance'

    case = models.ForeignKey(OffboardingCase, on_delete=models.CASCADE, related_name='steps')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    document = models.ForeignKey('hris.HRDocument', null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='offboarding_steps')
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='offboarding_steps_done')
    done_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['case', 'kind']
        constraints = [models.UniqueConstraint(fields=['case', 'kind'],
                                               name='uniq_offboarding_step_kind')]

    def __str__(self):
        return f'{self.case_id} {self.kind}'


class EmployeeAcknowledgement(BaseModel):
    """Something a new joiner (and sometimes the manager) must sign in Omni:
    the job description, each company policy (within 30 days), a monthly review."""

    class Kind(models.TextChoices):
        JOB_DESCRIPTION = 'job_description', 'Job description'
        POLICY          = 'policy',          'Company policy'
        MONTHLY_REVIEW  = 'monthly_review',  'Monthly review'

    employee = models.ForeignKey('payroll.Employee', on_delete=models.CASCADE,
                                 related_name='acknowledgements')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=200)
    document = models.ForeignKey('hris.HRDocument', null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='acknowledgements')
    period = models.CharField(max_length=7, blank=True, default='',
                              help_text='YYYY-MM for a monthly review.')
    due_date = models.DateField(null=True, blank=True)
    needs_manager = models.BooleanField(default=False)
    employee_signed_at = models.DateTimeField(null=True, blank=True)
    manager_signed_at = models.DateTimeField(null=True, blank=True)
    manager_signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['due_date', 'title']

    def __str__(self):
        return f'{self.employee_id} {self.kind} {self.title}'


class ProbationDecision(AuditableMixin, BaseModel):
    """Manager/HR answer at the end of probation (ELRA s.155: max 6 months)."""

    class Decision(models.TextChoices):
        CONFIRM = 'confirm', 'Confirm the appointment'
        EXTEND  = 'extend',  'Extend probation'
        END     = 'end',     'End employment'

    contract = models.ForeignKey('payroll.EmploymentContract', on_delete=models.CASCADE,
                                 related_name='probation_decisions')
    decision = models.CharField(max_length=10, choices=Decision.choices)
    new_probation_end = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True, default='')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']


class OfferLetter(AuditableMixin, BaseModel):
    """Offer letter drafted from an approved Authority to Recruit (HC sends it)."""

    class Status(models.TextChoices):
        DRAFT    = 'draft',    'Draft'
        SENT     = 'sent',     'Sent by HC'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'

    authority = models.OneToOneField('recruitment.AuthorityToRecruit', on_delete=models.CASCADE,
                                     related_name='offer_letter')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    candidate_email = models.EmailField(blank=True, default='')
    start_date = models.DateField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    onboarding_request = models.ForeignKey('hris.OnboardingRequest', null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']


class RoleSystemRequirement(BaseModel):
    """Which systems IT must set up for a department (HC: 'depending on role, notify IT
    of their systems requirements, e.g. Underwriting needs Graphite at the level of hire,
    Time Doctor'). HR edits these on HR Settings."""

    department = models.CharField(max_length=100, unique=True,
                                  help_text="Department name, or '*' for every new joiner.")
    systems = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['department']

    def __str__(self):
        return self.department


class LoginClassification(BaseModel):
    """HR's answer for an Omni login that has no payroll record behind it."""

    class Kind(models.TextChoices):
        STAFF   = 'staff',   'Our staff (fix payroll record)'
        PARTNER = 'partner', 'Partner / external — keep'
        SERVICE = 'service', 'Service / system account — keep'
        CLOSE   = 'close',   'Unknown — close the login'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='hr_login_classification')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    note = models.CharField(max_length=300, blank=True, default='')
    classified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='+')
