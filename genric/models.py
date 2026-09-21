"""genric/models.py — one row per press of the button.

A GENRIC pack is a regulatory submission. Six months later somebody will ask
"where did R5,656.30 come from" and the answer has to be a record, not a
re-run: the book is overwritten nightly and a re-run gives today's position,
not July's. So the run stores the manifest and the headline figures as
generated, alongside which bank statement and which export file fed them.

NOTHING here posts a journal or touches a GL mapping. The pack reads and
reports. (Hard stop, build spec: no journal posting and no GL mapping change
without the CFO signing off first.)
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import BaseModel


class GenricPackRun(BaseModel):
    """One generated GENRIC monthly pack."""

    class Status(models.TextChoices):
        GENERATING = 'generating', 'Generating'
        COMPLETE = 'complete', 'Complete'
        PARTIAL = 'partial', 'Complete with blocked reports'
        FAILED = 'failed', 'Failed'

    period_year = models.IntegerField()
    period_month = models.IntegerField()

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='genric_pack_runs',
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.GENERATING)

    # ── Input provenance — what this pack was built from ───────────────────
    bank_statement = models.ForeignKey(
        'banking.BankStatement', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='genric_pack_runs',
    )
    policy_source = models.CharField(
        max_length=40, blank=True,
        help_text='graphite_export | graphite_ro',
    )
    policy_export_name = models.CharField(max_length=255, blank=True)
    prior_export_name = models.CharField(max_length=255, blank=True)

    # ── Headline figures, as generated ─────────────────────────────────────
    #: Collection charges keyed in at generate time. Stored because a later
    #: download must re-render the SAME pack — a re-run with charges back at
    #: zero would hand Finance a different invoice under the same number.
    collection_charges = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    confirmed_gwp_incl_vat = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_reinsurance_premium_due = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invoice_number = models.CharField(max_length=30, blank=True)

    #: The full manifest: one entry per report, with its status and figures.
    manifest = models.JSONField(default=dict, blank=True)

    #: Anything that could not be produced, and why — in plain words.
    blocked_notes = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-period_year', '-period_month', '-generated_at']
        indexes = [models.Index(fields=['period_year', 'period_month'])]

    def __str__(self):
        return f'GENRIC pack {self.period_year}-{self.period_month:02d} ({self.status})'

    @property
    def period_label(self) -> str:
        import calendar
        return f'{calendar.month_name[self.period_month]} {self.period_year}'


class GenricSetting(BaseModel):
    """One named GENRIC config value, editable on screen without a deploy.

    Same shape and same contract as ``payroll.models.PayrollSetting``: the value
    is always stored as text and ``genric.config`` parses it to the type the
    caller expects. Rows are seeded lazily from ``genric.config._DEFAULTS`` on
    first read, so an upgrade never needs a data migration and a value Finance
    has already edited is never overwritten.

    Why a row and not an environment variable: the pay window is a business
    decision that must be changeable without a deploy, and the change has to be
    visible and audited. An env var needs a release, a restart and a person with
    server access, so in practice it never gets changed — and nobody reading the
    report can see what it is set to.

    Who can change it is an ACCESS question, not a code one: the admin needs
    ``is_staff``, which today only the CFO's mailbox gets. See genric/config.py.
    """

    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=300, blank=True)
    description = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['key']
        verbose_name = 'GENRIC Setting'
        verbose_name_plural = 'GENRIC Settings'

    def __str__(self):
        return f'{self.key} = {self.value}'
