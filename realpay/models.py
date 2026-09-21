"""
realpay/models.py — RealPay debit-order collection reports per month.

Storage shape (one row per month per beneficiary user):
  • period_year + period_month   — Jul-2025 onwards
  • beneficiary_user_id          — 19413 (BW UAT), 16244 / 24936 / 28555 (prod)
  • raw_payload                  — JSON dump of transactions pulled from RealPay
  • xlsx_file                    — generated XLSX, downloadable from omni
  • totals (collected / failed / net) for fast list rendering
  • ai_commentary                — DeepSeek-generated month narrative
  • status: pending | pulled | analysed | failed
  • pulled_at, analysed_at       — audit timestamps

Backfill flow: management command pull_realpay_history --from 2025-07-01
iterates calendar months, calls realpay.services.pull_month for each,
then runs realpay.services.generate_commentary.

Future Phase-2: nightly cron pulls just the current open month +
re-runs commentary on close-of-month.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


ZERO = Decimal('0.00')


class RealPayMonthlyReport(BaseModel):
    """One row per (year, month, beneficiary_user)."""

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending pull'
        PULLED    = 'pulled',    'Pulled, awaiting analysis'
        ANALYSED  = 'analysed',  'Analysed'
        FAILED    = 'failed',    'Failed'

    period_year         = models.PositiveSmallIntegerField()
    period_month        = models.PositiveSmallIntegerField()
    beneficiary_user_id = models.CharField(max_length=20)
    beneficiary_label   = models.CharField(max_length=80, blank=True, default='')

    status              = models.CharField(
                              max_length=12, choices=Status.choices,
                              default=Status.PENDING,
                          )

    # Counters (kept as Decimal for fast list rendering — no aggregate query).
    txn_count_total     = models.PositiveIntegerField(default=0)
    txn_count_successful = models.PositiveIntegerField(default=0)
    txn_count_failed    = models.PositiveIntegerField(default=0)

    amount_collected    = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    amount_failed       = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    amount_net          = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    raw_payload         = models.JSONField(default=dict, blank=True,
                                           help_text='Raw transaction list from RealPay.')
    xlsx_file           = models.FileField(
                              upload_to='realpay/%Y/%m/',
                              null=True, blank=True,
                              help_text='Generated XLSX for download.',
                          )
    ai_commentary       = models.TextField(blank=True, default='')

    pulled_at           = models.DateTimeField(null=True, blank=True)
    analysed_at         = models.DateTimeField(null=True, blank=True)
    pulled_by           = models.ForeignKey(
                              User, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='+',
                          )
    error_log           = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-period_year', '-period_month', 'beneficiary_user_id']
        unique_together = [('period_year', 'period_month', 'beneficiary_user_id')]
        verbose_name = 'RealPay monthly report'
        verbose_name_plural = 'RealPay monthly reports'
        indexes = [
            models.Index(fields=['period_year', 'period_month']),
            models.Index(fields=['beneficiary_user_id']),
        ]

    def __str__(self):
        return f'RealPay {self.period_year}-{self.period_month:02d} · user {self.beneficiary_user_id}'

    @property
    def period_label(self) -> str:
        import calendar
        return f'{calendar.month_abbr[self.period_month]} {self.period_year}'


# ---------------------------------------------------------------------------
# RealPay Collections module — row-level data (CFO directive 2026-06-05).
# Powers two Data-Department views under Collections > Banking > RealPay:
#   Objective 1 — Failed / Error Debit Tracker (per-policy debit outcome +
#                 plain-language reason from the Response Code report).
#   Objective 2 — Collections Dashboard (collected amount by product grouping).
# Source build-spec: "REALPAY REPORT PROMPT" (Data Dept), validated against the
# live RealPay Transaction Report, Client Billing detail and Response Code CSVs.
# DATA PROTECTION: client_name is PII (DPA No.18/2024 + AD-POL-AI-GOV-001).
#   It is stored for the auth-gated banking view + export ONLY, and must NEVER
#   be sent to any AI pipeline / external API. The reason-join + grouping work
#   on client_number + result codes + aggregate amounts only.
# ---------------------------------------------------------------------------


def normalize_response_code(raw) -> str:
    """Canonical form of a RealPay result / response code so the three reports
    join despite different zero-padding.

    The Transaction Report stores `2` / `6`, Client Billing stores `00002` /
    `00045`, the Response Code report stores `02` / `45`. Strategy: if the code
    is all digits, strip leading zeros and re-pad to 2 (so 2 -> 02, 00045 -> 45,
    310 -> 310). Non-numeric markers (XX, CA, blank) and the success code 0/00
    are returned upper-cased / blanked — they are NOT failure-reason lookups.
    """
    s = ('' if raw is None else str(raw)).strip().upper()
    if not s:
        return ''
    if s.isdigit():
        n = int(s)
        if n == 0:
            return '00'          # success sentinel — never reason-looked-up
        return str(n).zfill(2)
    return s                     # XX, CA, alphanumerics — kept verbatim


class RealPayResponseCode(BaseModel):
    """Lookup of RealPay result-code meanings (Response Code report).

    Keyed by Product Code + Response Code because the same code repeats across
    products (e.g. 04 = PAYMENT STOPPED under both FNBNDOBW and RTFNBBW) — see
    build-spec OD-6. `code_norm` is the normalized join key (see
    normalize_response_code)."""

    product_code = models.CharField(max_length=20, db_index=True)
    response_code = models.CharField(max_length=10)
    code_norm     = models.CharField(max_length=10, db_index=True,
                                     help_text='Normalized response code (join key).')
    description   = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['product_code', 'code_norm']
        unique_together = [('product_code', 'response_code')]
        verbose_name = 'RealPay response code'
        verbose_name_plural = 'RealPay response codes'
        indexes = [
            models.Index(fields=['product_code', 'code_norm']),
            models.Index(fields=['code_norm']),
        ]

    def __str__(self):
        return f'{self.product_code}/{self.response_code} — {self.description}'

    def save(self, *args, **kwargs):
        self.code_norm = normalize_response_code(self.response_code)
        super().save(*args, **kwargs)


class RealPayTransaction(BaseModel):
    """One row-level RealPay debit line.

    Two sources share the table (discriminated by `source`) because they are
    different RealPay reports at different grains:
      • 'transaction' — RealPay Transaction Report -> Objective 1 (debit outcome
        per policy, status, reason). Has Current Status + Result + Client Bank;
        NO product code (the Transaction Report does not expose one — OD-6).
      • 'billing'     — Client Billing detail -> Objective 2 (collected amount by
        client-number prefix). Has Product + AmountCollected.
    Endpoints filter by `source` so the two objectives never double-count."""

    class Source(models.TextChoices):
        TRANSACTION = 'transaction', 'Transaction Report'
        BILLING     = 'billing',     'Client Billing detail'

    source            = models.CharField(max_length=12, choices=Source.choices,
                                         db_index=True)
    txn_date          = models.DateField(null=True, blank=True, db_index=True,
                                         help_text='Installment Date (txn) / Transaction Date (billing).')
    tracking_start_date = models.DateField(
        null=True, blank=True, db_index=True,
        help_text=('Billing only — RealPay\'s Tracking Start Date, the month the '
                   'debit was TRACKED. A debit tracked in August often only '
                   'settles in the September report, so the month basis has to '
                   'come from this date and not from which report it arrived in '
                   '(Bokani, bug 6a48367f).'))
    client_number     = models.CharField(max_length=40, blank=True, default='', db_index=True)
    client_name       = models.CharField(max_length=160, blank=True, default='',
                                         help_text='PII — view/export only, never to any AI pipeline.')
    product           = models.CharField(max_length=40, blank=True, default='',
                                         help_text='RealPay product code (billing only; blank on txn report).')
    beneficiary_number = models.CharField(max_length=20, blank=True, default='')
    merchant          = models.CharField(max_length=120, blank=True, default='')
    contract_number   = models.CharField(max_length=40, blank=True, default='')
    contract_sequence = models.CharField(max_length=20, blank=True, default='')
    inst_seq          = models.CharField(max_length=20, blank=True, default='')

    installment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_amount       = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    amount_requested   = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    collected_amount   = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    current_status    = models.CharField(max_length=20, blank=True, default='', db_index=True,
                                         help_text='SUCCESSFUL/FAILED/PROCESSING/ERROR/CANCELLED.')
    result_code       = models.CharField(max_length=20, blank=True, default='')
    result_code_norm  = models.CharField(max_length=10, blank=True, default='', db_index=True)
    client_bank       = models.CharField(max_length=60, blank=True, default='')

    import_batch      = models.CharField(max_length=40, blank=True, default='',
                                         help_text='Importer run tag, for traceability.')

    class Meta(BaseModel.Meta):
        ordering = ['-txn_date', 'client_number']
        verbose_name = 'RealPay transaction'
        verbose_name_plural = 'RealPay transactions'
        indexes = [
            models.Index(fields=['source', 'txn_date']),
            models.Index(fields=['source', 'current_status']),
            models.Index(fields=['txn_date']),
            models.Index(fields=['client_number']),
            models.Index(fields=['result_code_norm']),
            # The dashboard selects a month on the tracking start date, which is
            # what makes the two-report combine a plain range filter.
            models.Index(fields=['source', 'tracking_start_date']),
        ]

    def __str__(self):
        return f'RealPay {self.source} {self.txn_date} {self.client_number} {self.current_status}'

    def save(self, *args, **kwargs):
        self.result_code_norm = normalize_response_code(self.result_code)
        super().save(*args, **kwargs)


class RealPayImportControl(BaseModel):
    """Per-import control totals (forensic-audit fix 2026-06-05).

    Stored at import time so the dashboard can prove its computed grand total
    reconciles to what was actually loaded from Client Billing — converting the
    self-referential "groups sum to total" check into a real control that
    detects silent row loss. One row per (source, batch)."""

    source        = models.CharField(max_length=12, db_index=True)
    batch         = models.CharField(max_length=40, blank=True, default='')
    row_count     = models.PositiveIntegerField(default=0)
    settled_count = models.PositiveIntegerField(default=0,
                        help_text='Rows with collected_amount > 0.')
    collected_sum = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO,
                        help_text='Sum of collected_amount over settled rows at load time.')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'RealPay import control'
        verbose_name_plural = 'RealPay import controls'

    def __str__(self):
        return f'RealPay control {self.source} {self.batch} rows={self.row_count} sum={self.collected_sum}'
