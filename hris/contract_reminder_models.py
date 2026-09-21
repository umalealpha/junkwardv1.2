"""Contract reminder + renewal-decision records (CFO HR plan 19-Sep-2026, item #01).

HC's rule (hc@ 19-Sep-2026): remind before a contract ends —
  employees 2 months · controllers 4 · C-suite 4 · senior managers 4 · expatriates 6.
The months live in ContractReminderRule so HR changes them in admin, no deploy.

Imported into hris/models.py so Django's app registry sees the models.
"""
from django.conf import settings
from django.db import models

from core.models import AuditableMixin, BaseModel


class ContractReminderRule(AuditableMixin, BaseModel):
    """How many months before the end date each group is reminded."""

    class Category(models.TextChoices):
        EXPATRIATE     = 'expatriate',     'Expatriate'
        CONTROLLER     = 'controller',     'Controller'
        C_SUITE        = 'c_suite',        'C-suite'
        SENIOR_MANAGER = 'senior_manager', 'Senior manager'
        EMPLOYEE       = 'employee',       'Employee'

    category = models.CharField(max_length=20, choices=Category.choices, unique=True)
    months_before = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['-months_before', 'category']
        verbose_name = 'Contract reminder rule'

    def __str__(self):
        return f'{self.get_category_display()}: {self.months_before} months'


class ContractRenewalDecision(AuditableMixin, BaseModel):
    """HR's recorded answer for an expiring contract. The latest row wins."""

    class Decision(models.TextChoices):
        RENEW_SAME = 'renew_same', 'Renew on the same terms'
        CHANGE     = 'change',     'Renew with changes'
        END        = 'end',        'End the contract'

    contract = models.ForeignKey(
        'payroll.EmploymentContract', on_delete=models.CASCADE,
        related_name='renewal_decisions',
    )
    decision = models.CharField(max_length=12, choices=Decision.choices)
    note = models.TextField(blank=True, default='')
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='contract_decisions',
    )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.contract_id}: {self.decision}'


class ContractReminderLog(BaseModel):
    """One row per reminder e-mail sent — makes the daily job idempotent."""

    class Stage(models.TextChoices):
        REMINDER   = 'reminder',   'Reminder'
        ESCALATION = 'escalation', 'Escalation to CFO'

    contract = models.ForeignKey(
        'payroll.EmploymentContract', on_delete=models.CASCADE,
        related_name='reminder_logs',
    )
    stage = models.CharField(max_length=12, choices=Stage.choices)
    sent_on = models.DateField()
    recipients = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-sent_on']


class HRSetting(BaseModel):
    """HR self-service settings (CFO 19-Sep-2026: "so they can run the whole HR
    dashboard without the CFO's support"). One row per key; HR heads edit them
    on /hris/settings. A locked row cannot change until a head unlocks it.

    Keys in use:
      hr_heads                    list of emails allowed to manage the HR team + settings
      contract_reminder_recipients list of HR emails that receive contract reminders
    """

    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField(default=list, blank=True)
    locked = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='hr_settings_updated',
    )

    class Meta(BaseModel.Meta):
        ordering = ['key']

    def __str__(self):
        return self.key
