"""
integrations/models.py

Stores every inbound event from Graphite (policy system) and bank APIs.
Each row is immutable once received — processing result is appended in place.
"""

import uuid

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


class IntegrationEvent(BaseModel):
    """
    Audit log + processing record for every external event received.
    """

    class SourceSystem(models.TextChoices):
        GRAPHITE  = 'graphite',  'Graphite (Policy System)'
        BANK_API  = 'bank_api',  'Bank API'

    class EventType(models.TextChoices):
        POLICY_ISSUED          = 'policy_issued',          'Policy Issued'
        POLICY_CANCELLED       = 'policy_cancelled',       'Policy Cancelled'
        CLAIM_APPROVED         = 'claim_approved',         'Claim Approved'
        COMMISSION_CALCULATED  = 'commission_calculated',  'Commission Calculated'
        BANK_TRANSACTION       = 'bank_transaction',       'Bank Transaction'

    class Status(models.TextChoices):
        RECEIVED   = 'received',   'Received'
        PROCESSING = 'processing', 'Processing'
        PROCESSED  = 'processed',  'Processed'
        FAILED     = 'failed',     'Failed'
        SKIPPED    = 'skipped',    'Skipped'

    class ResultType(models.TextChoices):
        INVOICE = 'invoice', 'Invoice'
        PAYMENT = 'payment', 'Payment'

    source_system = models.CharField(
        max_length=30,
        choices=SourceSystem.choices,
    )
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
    )
    event_data = models.JSONField(
        help_text='Full event payload as received from source system.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
        db_index=True,
    )
    error_message = models.TextField(blank=True, null=True)
    processed_at  = models.DateTimeField(null=True, blank=True)
    result_type   = models.CharField(
        max_length=20,
        choices=ResultType.choices,
        null=True, blank=True,
    )
    result_id     = models.UUIDField(null=True, blank=True)
    received_at   = models.DateTimeField(auto_now_add=True)
    retry_count   = models.IntegerField(default=0)

    # SECURITY / CONTROL (2026-08-25, Manus retest P0): processing an event
    # CREATES an invoice, vendor bill or credit note. A retried push, a network
    # retry, or a Graphite redelivery would have booked the same revenue twice
    # with nothing to stop it. The sender supplies its own event id here and we
    # refuse a second event carrying the same one.
    idempotency_key = models.CharField(
        max_length=128, blank=True, default='', db_index=True,
        help_text="Sender's own unique id for this event. A repeat push with the "
                  "same key returns the original event instead of creating a "
                  "second accounting record. Blank = not supplied (legacy).",
    )
    # Service attribution: which API key put this event in. `_system_user()`
    # stamps the first superuser as created_by on the records the processor
    # raises, so without this there was no way to tell WHICH integration asked.
    received_via = models.CharField(
        max_length=120, blank=True, default='',
        help_text='Label of the API key that submitted this event.',
    )

    class Meta:
        ordering = ['-received_at']
        indexes  = [
            models.Index(fields=['source_system', 'event_type']),
            models.Index(fields=['status']),
            # PERF (2026-07-17): the event store is append-only and unbounded;
            # the list view orders by -received_at, which was a filesort.
            models.Index(fields=['-received_at'], name='intgevent_received_at_idx'),
        ]
        constraints = [
            # Conditional, so the many legacy/blank rows do not collide with
            # each other — only a real supplied key is held unique.
            models.UniqueConstraint(
                fields=['idempotency_key'],
                condition=~models.Q(idempotency_key=''),
                name='intgevent_idempotency_key_uniq',
            ),
        ]

    def __str__(self):
        return f"{self.source_system}/{self.event_type} [{self.status}] {self.received_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------------
# Graphite V2 Finance feed — pulled payment transactions (read-only mirror)
# ---------------------------------------------------------------------------
# These two models back the machine-to-machine pull from the Graphite V2
# Finance API (see integrations/graphite_finance.py). They are a READ-ONLY
# local mirror used for reporting + reconciliation; nothing here posts to the
# GL (CFO directive 2026-06-15). Graphite is the system of record — omni rows
# are refreshed in place on every sweep (idempotent upsert on graphite_id).


class GraphitePaymentSyncRun(BaseModel):
    """One row per `pull_graphite_payments` invocation — the audit trail for
    every sweep of the Graphite Finance API."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        RUNNING = 'running', 'Running'
        SUCCESS = 'success', 'Success'
        PARTIAL = 'partial', 'Partial (some windows failed)'
        FAILED  = 'failed',  'Failed'
        SKIPPED = 'skipped', 'Skipped (not configured)'

    status        = models.CharField(max_length=10, choices=Status.choices,
                                     default=Status.PENDING, db_index=True)
    window_from   = models.DateField()
    window_to     = models.DateField()
    partner       = models.CharField(max_length=50, blank=True, default='')
    dry_run       = models.BooleanField(default=False)

    pages_fetched = models.PositiveIntegerField(default=0)
    rows_seen     = models.PositiveIntegerField(default=0)
    rows_created  = models.PositiveIntegerField(default=0)
    rows_updated  = models.PositiveIntegerField(default=0)

    error_message = models.TextField(blank=True, default='')
    started_at    = models.DateTimeField(null=True, blank=True)
    finished_at   = models.DateTimeField(null=True, blank=True)
    triggered_by_user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='graphite_payment_sync_runs',
    )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Graphite Payment Sync Run'
        verbose_name_plural = 'Graphite Payment Sync Runs'
        ordering            = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'],
                         name='intg_gpsr_status_idx'),
            models.Index(fields=['window_from', 'window_to'],
                         name='intg_gpsr_window_idx'),
        ]

    def __str__(self):
        return (f'sync {self.window_from}→{self.window_to} '
                f'[{self.status}] +{self.rows_created}/~{self.rows_updated}')


class GraphitePaymentTransaction(BaseModel):
    """Local mirror of one Graphite V2 `payment_transactions` row.

    Field names mirror the API's stable JSON schema (see
    PaymentTransactionController::shapeRow). `graphite_id` is the source PK and
    the idempotency key for upserts. `customer_name` / `agent_name` are
    DPA-regulated PII — kept for internal reconciliation only, never surfaced
    in external responses.
    """

    # Source identity / idempotency
    graphite_id      = models.BigIntegerField(unique=True, db_index=True,
                                              help_text='payment_transactions.id in Graphite V2')

    # Core transaction
    policy_number    = models.CharField(max_length=64, blank=True, default='', db_index=True)
    reference_number = models.CharField(max_length=191, blank=True, default='', db_index=True)
    amount           = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payment_method   = models.CharField(max_length=50, blank=True, default='',
                                        help_text='Payment partner: RealPay / DPO / Orange / manual / VCS / cash')
    status           = models.CharField(max_length=30, blank=True, default='',
                                        help_text='Raw status as shipped (mixed casing).')
    status_norm      = models.CharField(max_length=30, blank=True, default='',
                                        help_text='Lower-cased status for filtering.')
    is_refund        = models.BooleanField(default=False)
    is_reverse       = models.BooleanField(default=False)
    payment_frequency = models.CharField(max_length=30, blank=True, default='',
                                         help_text='FIRST / RECURRING / ...')
    note             = models.TextField(blank=True, default='')

    # Timestamps (from source). source_recorded_at == Graphite created_at,
    # the field the API windows on.
    paid_at            = models.DateTimeField(null=True, blank=True)
    paid_on            = models.DateTimeField(null=True, blank=True)
    source_recorded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_updated_at  = models.DateTimeField(null=True, blank=True)

    # Policy context (denormalised from the API join)
    policy_id    = models.BigIntegerField(null=True, blank=True)
    product_id   = models.BigIntegerField(null=True, blank=True)
    plan_id      = models.BigIntegerField(null=True, blank=True)
    product_name = models.CharField(max_length=191, blank=True, default='')
    plan_name    = models.CharField(max_length=191, blank=True, default='')

    # PII — internal reconciliation only
    customer_name = models.CharField(max_length=191, blank=True, default='')
    agent_name    = models.CharField(max_length=191, blank=True, default='')

    # DPO gateway refs
    dpo_trans_id    = models.CharField(max_length=191, blank=True, default='')
    dpo_company_ref = models.CharField(max_length=191, blank=True, default='')
    dpo_token       = models.CharField(max_length=191, blank=True, default='')

    # Provenance
    raw       = models.JSONField(default=dict, blank=True,
                                 help_text='Full API row as received.')
    sync_run  = models.ForeignKey(
        GraphitePaymentSyncRun, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='transactions',
    )
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Graphite Payment Transaction'
        verbose_name_plural = 'Graphite Payment Transactions'
        ordering            = ['-source_recorded_at', '-graphite_id']
        indexes = [
            models.Index(fields=['payment_method', '-source_recorded_at'],
                         name='intg_gpt_method_idx'),
            models.Index(fields=['status_norm', '-source_recorded_at'],
                         name='intg_gpt_statusn_idx'),
            models.Index(fields=['is_refund'], name='intg_gpt_refund_idx'),
        ]

    def __str__(self):
        return f'GPT#{self.graphite_id} {self.payment_method} {self.amount} [{self.status}]'


class TimeDoctorDailySnapshot(BaseModel):
    """One privacy-safe daily aggregate of Time Doctor workforce data.

    Stores per-user hours + productivity % + attendance (the `payload`) and the
    company `totals`. Raw window/app titles are NEVER stored (AD-POL-AI-GOV-001)
    — only aggregated seconds per productivity bucket. One row per (company, as_of).
    Written by the `pull_timedoctor` management command; read by the omni
    Time Doctor report page + the daily email.
    """

    company_id = models.CharField(max_length=64, db_index=True)
    as_of      = models.DateField(db_index=True, help_text='The day these figures cover.')
    totals     = models.JSONField(default=dict, help_text='Company roll-up (hours, productivity %, counts).')
    payload    = models.JSONField(default=list, help_text='Per-user aggregates (no raw titles).')
    emailed    = models.BooleanField(default=False)
    # Multi-pull settle samples for the people-data guardrail (CFO 2026-08-01).
    # {slot_label: {td_user_id: tracked_seconds}} — the SAME day pulled several
    # times (03:00 / 04:00 / 08:30) so hris.people_data_guard can tell a figure
    # that has stopped moving from one that's still arriving. Aggregate seconds
    # only, no raw titles (AD-POL-AI-GOV-001). One slot key per pull; a fresh
    # `as_of` row starts empty. See hris.people_data_guard.
    settle_samples = models.JSONField(default=dict, blank=True,
                                      help_text='{pull_slot: {td_user_id: tracked_seconds}} for the settle gate.')
    # td_user_ids actually PUBLISHED as "did not track" for this day (guardrail
    # survivors). Read the morning after to catch anyone whose hours arrived late
    # and send a one-line correction (CFO 2026-08-01). See hris.exceptions_report.
    reported_no_track = models.JSONField(default=list, blank=True,
                                         help_text='td_user_ids named as did-not-track (for next-day correction).')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Time Doctor Daily Snapshot'
        verbose_name_plural = 'Time Doctor Daily Snapshots'
        ordering            = ['-as_of']
        constraints = [
            models.UniqueConstraint(fields=['company_id', 'as_of'],
                                    name='intg_td_company_asof_uniq'),
        ]

    def __str__(self):
        t = self.totals or {}
        return f'TimeDoctor {self.as_of} — {t.get("active_users","?")} active, {t.get("total_hours","?")}h'


class TimeDoctorUserMap(BaseModel):
    """Persistent Time Doctor user_id ↔ payroll Employee link (Fable review
    2026-07-14). TD user_id is stable, so a confirmed row ends the whole
    name-matching bug class for that person. Rows are auto-suggested by
    integrations.td_matching.TDMatcher (source=auto, confirmed=False) and
    become authoritative once a human confirms them. employee NULL = a TD
    account with no payroll match yet (the review queue) — genuinely
    non-payroll accounts (contractors / role / test rigs) simply stay there
    and are never reported on."""

    class Source(models.TextChoices):
        AUTO   = 'auto',   'Auto-suggested by the matcher'
        MANUAL = 'manual', 'Set by a human'

    td_user_id = models.CharField(max_length=64, unique=True, db_index=True,
                                  help_text='Time Doctor user id (stable).')
    td_name    = models.CharField(max_length=191, blank=True, default='')
    td_email   = models.CharField(max_length=191, blank=True, default='')
    employee   = models.ForeignKey('payroll.Employee', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='td_maps')
    confirmed  = models.BooleanField(default=False,
                                     help_text='Confirmed by a human — the matcher trusts this above all name matching.')
    source     = models.CharField(max_length=10, choices=Source.choices, default=Source.AUTO)
    updated_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Time Doctor User Map'
        verbose_name_plural = 'Time Doctor User Map'
        ordering            = ['td_name']
        indexes = [models.Index(fields=['confirmed'], name='intg_tdmap_confirmed_idx')]

    def __str__(self):
        who = getattr(self.employee, 'full_name', None) or '(unmatched)'
        flag = '✓' if self.confirmed else '?'
        return f'{self.td_name or self.td_user_id} → {who} [{flag}]'


class GraphiteClaim(BaseModel):
    """Read-only mirror of a Graphite V2 claim (GET /api/v1/claims), so omni can
    show the full claims register on one screen with filters + export — instead
    of going claim-by-claim in Graphite (Bokani 2026-06-24). No GL involvement.
    Upserts on graphite_id via pull_graphite_claims. `updated_at` = last synced."""

    graphite_id          = models.BigIntegerField(unique=True, db_index=True)
    claim_number         = models.CharField(max_length=64, blank=True, default='', db_index=True)
    claim_type           = models.CharField(max_length=64, blank=True, default='', db_index=True)
    status               = models.CharField(max_length=32, blank=True, default='', db_index=True)
    claim_handler        = models.CharField(max_length=128, blank=True, default='')
    customer_name        = models.CharField(max_length=255, blank=True, default='')
    customer_graphite_id = models.BigIntegerField(null=True, blank=True)
    is_company           = models.BooleanField(default=False)
    policy_number        = models.CharField(max_length=64, blank=True, default='', db_index=True)
    product_name         = models.CharField(max_length=128, blank=True, default='')
    registered_date      = models.DateField(null=True, blank=True, db_index=True)
    graphite_created_at  = models.DateTimeField(null=True, blank=True)
    # Claim-detail figures (Bokani 2026-06-24) — from GET /api/v1/claims/{id}.
    # Graphite stores these as whole Pula (int) — e.g. 1390 = P1,390.00.
    date_of_loss         = models.DateField(null=True, blank=True, db_index=True)
    total_reserve        = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_payment        = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    balance              = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    # Damage cause / what happened (Bokani 2026-06-25) — from the claim detail's
    # type_of_loss (fallback description_of_loss / event_name).
    damage_cause         = models.CharField(max_length=200, blank=True, default='')
    detail_synced_at     = models.DateTimeField(null=True, blank=True)
    # AI claim summary (CFO 2026-08-31) — a <=200-word plain-English read of the
    # claim plus a SOFT pay/hold opinion, written off-peak by our own AI over
    # PII-redacted facts (presummarise_open_claims). ADVISORY ONLY: the figures
    # and the hard flags are computed by rule (never by the AI), and the AI never
    # decides a claim. Stored per-claim so one summary serves every payment
    # request that references the claim. Empty until the off-peak job has run.
    ai_summary           = models.TextField(blank=True, default='')
    ai_suggestion        = models.CharField(max_length=8, blank=True, default='')   # PAY | HOLD | ''
    ai_reason            = models.CharField(max_length=200, blank=True, default='')
    ai_engine            = models.CharField(max_length=40, blank=True, default='')
    ai_summary_at        = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Graphite Claim'
        verbose_name_plural = 'Graphite Claims'
        ordering            = ['-registered_date', '-graphite_created_at']
        indexes = [
            models.Index(fields=['status', 'claim_type'], name='intg_gclaim_st_ty_idx'),
            models.Index(fields=['-registered_date'], name='intg_gclaim_regdate_idx'),
        ]

    def __str__(self):
        return f'Claim {self.claim_number or self.graphite_id} ({self.status})'


# ---------------------------------------------------------------------------
# Graphite -> Omni analytics feed (CFO 2026-08-06, Pramod's Appendix A v1)
# ---------------------------------------------------------------------------
class GraphiteSnapshot(BaseModel):
    """One dataset pushed from Graphite's Alpha Brain analytics into Omni.

    Deliberately inert. This table is a letterbox: Graphite drops a snapshot in,
    Omni reads it. Nothing in Omni acts on the contents, nothing writes back to
    Graphite, and no automation is triggered by an arrival. The CFO's standing
    rule on Alpha Brain is arms-off, and a feed that could set something running
    is not arms-off.

    Snapshot-replace, per the agreed contract: the latest push for a dataset is
    the truth and the previous one is discarded. That keeps this from silently
    growing into a second copy of Graphite's history that nobody reconciles.
    """

    dataset      = models.CharField(
        max_length=64, unique=True, db_index=True,
        help_text='Stable key for the tab, e.g. weekly_update, major_claims.')
    payload      = models.JSONField(default=dict)
    row_count    = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(
        max_length=64, blank=True, default='',
        help_text='SHA-256 of the body, so a re-sent identical snapshot is '
                  'recognised instead of counted as fresh data.')
    received_at  = models.DateTimeField(auto_now=True)
    source_ip    = models.CharField(max_length=45, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['dataset']
        verbose_name = 'Graphite snapshot'

    def __str__(self):
        return f'{self.dataset} ({self.row_count} rows, {self.received_at:%Y-%m-%d %H:%M})'


# ---------------------------------------------------------------------------
# ClaimStatusCode — one-time email code for the PUBLIC "check my claim" line
# ---------------------------------------------------------------------------
class ClaimStatusCode(BaseModel):
    """A short-lived 6-digit code emailed to the address ON FILE for a claim,
    so a customer can view that claim's status without a login.

    Only the SHA-256 hash of the code is stored. Codes are single-use,
    attempt-limited and expire in minutes. The `email` field records the
    on-file address the code was sent to (audit only) — never a caller value.
    Keyed on claim_number (not a Django User / RewardMember). Mirrors the
    proven pattern in rewards/customer_auth.py.
    """
    claim_number = models.CharField(max_length=64, db_index=True)
    email        = models.EmailField(help_text='On-file address the code was sent to (audit).')
    code_hash    = models.CharField(max_length=64)
    expires_at   = models.DateTimeField()
    attempts     = models.PositiveIntegerField(default=0)
    consumed     = models.BooleanField(default=False)
    sent_ip      = models.CharField(max_length=45, blank=True, default='')

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=['claim_number', 'consumed', 'expires_at'])]
        verbose_name = 'Claim-status code'

    def __str__(self):
        flag = 'used' if self.consumed else 'live'
        return f'{self.claim_number} ({flag}, exp {self.expires_at:%H:%M})'


# ---------------------------------------------------------------------------
# Screen-integrity monitoring (CFO 2026-09-06)
# ---------------------------------------------------------------------------
# Persisted output of the frozen-screen / weight-on-a-key detector
# (integrations.td_screenshot_integrity) so the CFO can PULL exceptions from an
# Omni screen instantly instead of re-running the Time Doctor pull each time.
# The daily cron writes one ScreenIntegrityScan per Botswana calendar day plus a
# ScreenIntegrityFlag row per WATCH/SUSPICIOUS person. Privacy (AD-POL-AI-GOV-001):
# names + counts/percentages only — no window titles, no images, no md5s.

class ScreenIntegrityScan(BaseModel):
    """One day's whole-company frozen-screen sweep summary. Silence never means
    'not checked': every scanned day has a row, even a clean one (flagged=0)."""

    class Status(models.TextChoices):
        OK      = 'ok',      'Ran'
        NO_DATA = 'no_data', 'No screenshots'
        FAILED  = 'failed',  'Pull failed'

    day            = models.DateField(unique=True, db_index=True,
                        help_text='Botswana calendar day scanned.')
    people_checked = models.PositiveIntegerField(default=0)
    suspicious     = models.PositiveIntegerField(default=0)
    watch          = models.PositiveIntegerField(default=0)
    status         = models.CharField(max_length=8, choices=Status.choices,
                        default=Status.OK)
    note           = models.CharField(max_length=200, blank=True, default='')
    ran_at         = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-day']
        verbose_name = 'Screen-integrity scan'

    def __str__(self):
        return f'{self.day}: {self.suspicious} susp / {self.watch} watch of {self.people_checked}'


class ScreenIntegrityFlag(BaseModel):
    """One flagged person on one day. Only WATCH/SUSPICIOUS people get a row."""

    scan          = models.ForeignKey(ScreenIntegrityScan, on_delete=models.CASCADE,
                        related_name='flags')
    day           = models.DateField(db_index=True)
    td_user_id    = models.CharField(max_length=64, blank=True, default='')
    name          = models.CharField(max_length=120, blank=True, default='')
    suspicion     = models.CharField(max_length=12, db_index=True)   # watch | suspicious
    shots         = models.PositiveIntegerField(default=0)
    frozen_typing_pct   = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    frozen_typing_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    mouse_dead_pct      = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    identical_pct       = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    reasons       = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-day', 'suspicion', '-frozen_typing_pct']
        indexes = [models.Index(fields=['day', 'suspicion'])]
        constraints = [
            models.UniqueConstraint(fields=['day', 'td_user_id'],
                                    name='uniq_screenflag_day_user'),
        ]
        verbose_name = 'Screen-integrity flag'

    def __str__(self):
        return f'{self.day} {self.name} [{self.suspicion}]'


class RealpayReconSnapshot(BaseModel):
    """One morning's answer to "did RealPay's collections reach our ledger?".

    WHY IT IS STORED. The comparison groups two months of a 6.6-million-row feed per
    month and measured 23.5 seconds against the live replica. A 15-minute in-memory
    cache made repeat views instant but the server runs four worker processes, so up to
    four readers a quarter-hour still waited the full 23 seconds — and the QC would flag
    the screen as slow every night, which trains people to ignore the alarm on the very
    page built to raise one.

    So the 06:00 job writes its result here and the screen reads the saved copy with the
    time it was computed shown beside it. Pressing Refresh still computes live.

    APPEND ONLY. A correction is a new row, never an overwrite, so the history of what
    we believed on each morning survives — the same rule Build Brief 1 sets for its
    daily snapshot, and for the same reason: a figure that silently restates cannot be
    audited.
    """

    asof            = models.DateField(db_index=True)
    window_months   = models.PositiveSmallIntegerField(default=6)
    verdict         = models.CharField(max_length=8, db_index=True)   # ok | break
    summary         = models.TextField(blank=True, default='')
    total_shortfall = models.PositiveIntegerField(default=0)
    months_breached = models.PositiveSmallIntegerField(default=0)
    #: The whole compare() result, exactly as the screen and the email consumed it.
    payload         = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [models.Index(fields=['-created_at']),
                   models.Index(fields=['asof', 'verdict'])]
        verbose_name = 'RealPay reconciliation snapshot'

    def __str__(self):
        return f'{self.asof} {self.verdict} — {self.total_shortfall:,} missing'
