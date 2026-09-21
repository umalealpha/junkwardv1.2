"""
fnb/models.py

FNB Botswana banking integration — audit + state models.

Three responsibilities:

  - FNBSyncLog          Every API call we make to FNB and every webhook we
                        receive is logged here. Auditor replay during any
                        future fraud investigation reads from this single table.
  - FNBBatchSubmission  Outbound EFT batches we submit to FNB. Idempotency
                        key prevents double-submission; status tracks the
                        full life-cycle (pending → submitted → acknowledged →
                        completed / failed).
  - FNBWebhookEvent     Inbound webhooks from FNB (transaction confirmations,
                        balance updates, batch acks). Stored raw + parsed.

The ACTUAL endpoints / payload formats are still TBD — Boitumelo at FNB
Botswana is preparing the spec. The models here are intentionally
endpoint-agnostic; they record what the service called and what came
back, regardless of REST shape.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


# ---------------------------------------------------------------------------
# FNBSyncLog — one row per outbound API call OR inbound webhook
# ---------------------------------------------------------------------------

class FNBSyncLog(BaseModel):

    class Direction(models.TextChoices):
        OUTBOUND = 'outbound', 'Outbound (we → FNB)'
        INBOUND  = 'inbound',  'Inbound (FNB → us)'

    class Service(models.TextChoices):
        STATEMENT     = 'statement',     'Bank statement pull'
        PAYMENT_BATCH = 'payment_batch', 'EFT batch submission'
        PAYMENT_ACK   = 'payment_ack',   'Payment acknowledgement'
        BALANCE       = 'balance',       'Account balance check'
        BENEFICIARY   = 'beneficiary',   'Beneficiary sync'
        NOTIFICATION  = 'notification',  'Notification feed pull'
        TXN_HISTORY   = 'txn_history',   'Transaction history pull'
        WEBHOOK       = 'webhook',       'Generic webhook'
        AUTH          = 'auth',          'Auth / token'
        TEST          = 'test',          'Connection test'
        OTHER         = 'other',         'Other'

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        SUCCESS   = 'success',   'Success'
        FAILED    = 'failed',    'Failed'
        TIMEOUT   = 'timeout',   'Timed out'
        SKIPPED   = 'skipped',   'Skipped (not configured)'
        # HTTP 425 "Too Early" is FNB saying "I have not processed that batch
        # yet" - the normal answer while a batch waits for authorisation, and
        # a healthy poll can see hundreds before the single 200 that finds it
        # settled. Recording those as FAILED put 49,915 phantom failures into
        # seven days of monitoring while the real count of non-425 failures
        # was ZERO (executive review 2026-09-12). A waiting answer is not an
        # error and must never be counted as one.
        WAITING   = 'waiting',   'Waiting — the bank has not processed it yet'

    direction      = models.CharField(max_length=10, choices=Direction.choices)
    service        = models.CharField(max_length=20, choices=Service.choices,
                                      default=Service.OTHER)
    status         = models.CharField(max_length=10, choices=Status.choices,
                                      default=Status.PENDING)

    endpoint       = models.CharField(max_length=300, blank=True, default='',
                                      help_text='URL path or webhook source.')
    http_method    = models.CharField(max_length=10, blank=True, default='')
    http_status    = models.PositiveIntegerField(null=True, blank=True)
    elapsed_ms     = models.PositiveIntegerField(default=0)

    request_summary  = models.TextField(blank=True, default='',
                                        help_text='Short human-readable description '
                                                  '(e.g. "pull statements 1110 from '
                                                  '2026-05-01 to 2026-05-09").')
    request_payload  = models.JSONField(default=dict, blank=True,
                                        help_text='Outbound request body, redacted '
                                                  'of secrets.')
    response_payload = models.JSONField(default=dict, blank=True,
                                        help_text='Inbound response body or webhook '
                                                  'event, raw.')
    error_message    = models.TextField(blank=True, default='')

    # Optional FK to richer parent objects when relevant
    related_batch       = models.ForeignKey(
                              'fnb.FNBBatchSubmission', null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='sync_logs',
                          )
    triggered_by_user   = models.ForeignKey(
                              User, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='fnb_sync_logs',
                          )

    class Meta(BaseModel.Meta):
        verbose_name        = 'FNB Sync Log'
        verbose_name_plural = 'FNB Sync Logs'
        ordering            = ['-created_at']
        indexes = [
            models.Index(fields=['direction', '-created_at']),
            models.Index(fields=['service', '-created_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.direction} {self.service} {self.status} {self.created_at}'


# ---------------------------------------------------------------------------
# FNBBatchSubmission — outbound EFT batches
# ---------------------------------------------------------------------------

class FNBBatchSubmission(BaseModel):

    class Status(models.TextChoices):
        PENDING       = 'pending',       'Pending submission'
        SUBMITTED     = 'submitted',     'Submitted to FNB'
        ACKNOWLEDGED  = 'acknowledged',  'Acknowledged by FNB'
        SETTLED       = 'settled',       'Settled (funds debited)'
        FAILED        = 'failed',        'Failed (rejected — no money moved)'
        # The POST left but we never got a clean response (timeout / 5xx). The
        # money MAY have moved — a human must verify with FNB (retrieveReport
        # on the same idempotency key) before any resubmit. NEVER auto-retry.
        UNKNOWN       = 'unknown',       'Unknown — verify with FNB before resubmit'
        CANCELLED     = 'cancelled',     'Cancelled before submission'

    #: The statuses that mean THE MONEY DID NOT MOVE. One definition, because
    #: this tuple had already been written out by hand in four places and they
    #: had drifted: payment_views._batches_all_rejected carried 'cancelled'
    #: (Fable 5.1, 17-Sep-2026 — "the instruction never left us, so the money
    #: certainly did not move") and payment_duplicates did not, which quietly
    #: dropped every batch cancelled before submission out of the duplicate
    #: control. A shared constant makes leaving one out a deliberate act.
    #:
    #: 'unknown' belongs here in the sense that matters: the POST left us and
    #: the bank never answered cleanly, so the money MAY have moved — which is
    #: a reason for a person to look, never a reason to assume it is settled.
    DID_NOT_MOVE = ('failed', 'unknown', 'cancelled')

    # Idempotency — if we retry, FNB sees the same key and returns the
    # original response instead of double-debiting us. Format up to caller;
    # current scheme: `{payee-or-count} {seq} (O)` (≤35 chars), e.g.
    # `Grand RE-Radical 000044 (O)` — the payee plus the Omni reference sequence
    # tail, readable on FNB's batch list (CFO 2026-08-24: no random hex, show the
    # payee). Unique because the sequence comes from the unique payment_number;
    # a `-N` suffix disambiguates a resubmit of the same payment set. Not a daily
    # sequence in the old sense: the old count()+1 scheme reused -000001 after a
    # rolled-back batch and FNB rejected five 16-Jul-2026 submissions as
    # duplicate messageIds (Kabelo Sekoto, RMB, 2026-08-20).
    idempotency_key  = models.CharField(max_length=64, unique=True)

    source_account   = models.ForeignKey(
                           'banking.BankAccount', on_delete=models.PROTECT,
                           related_name='fnb_batches',
                           help_text='The Alpha Direct FNB account being debited.',
                       )
    payment_count    = models.PositiveIntegerField(default=0)
    total_amount_bwp = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency_code    = models.CharField(max_length=3, default='BWP')
    # Real link to the payments in this batch (Fable H3) — so a reject can
    # unstamp them, and an investigation can answer "which payments were in
    # batch X?" without string-parsing the payload snapshot. Empty for the
    # Quick-Transfer flow (transient synthetic payment, no DB row).
    payments         = models.ManyToManyField('payments.Payment', blank=True,
                                              related_name='fnb_batches')

    status           = models.CharField(max_length=15, choices=Status.choices,
                                        default=Status.PENDING)
    submitted_at     = models.DateTimeField(null=True, blank=True)
    acknowledged_at  = models.DateTimeField(null=True, blank=True)
    settled_at       = models.DateTimeField(null=True, blank=True)

    fnb_reference    = models.CharField(max_length=100, blank=True, default='',
                                        help_text='Reference issued by FNB on '
                                                  'acknowledgement (their batch ID).')
    failure_reason   = models.TextField(blank=True, default='')

    # Snapshot of the payload we sent — for audit replay
    payload_snapshot = models.JSONField(default=dict, blank=True)
    submitted_by     = models.ForeignKey(
                           User, null=True, blank=True,
                           on_delete=models.SET_NULL,
                           related_name='fnb_batches_submitted',
                       )
    # The payment request this batch was loaded from — set on EVERY batch the
    # request produces (CFO 2026-09-17, the FNB incremental controls).
    #
    # WHY IT IS NEEDED even though PaymentRequest already has an `fnb_batch`
    # FK: that FK is ONE batch, and a request with processing_method
    # 'individual' produces ONE BATCH PER LINE. On 16-Sep-2026 a single
    # Choppies supplier request split into four FNB instructions and all four
    # were rejected AG01 — but the request pointed at only the first, so the
    # three-way check and the exception cockpit could see one failure out of
    # four, and a reader saw four unrelated problems on the batch list with no
    # way to tell they were one supplier and one cause.
    #
    # Deliberately additive and nullable: every batch created before today,
    # and the Quick-Transfer flow (which has no request at all), keep a null
    # here. Nothing reads it as "no request means something is wrong".
    payment_request  = models.ForeignKey(
                           'taskboard.PaymentRequest', null=True, blank=True,
                           on_delete=models.SET_NULL,
                           related_name='fnb_batches',
                           help_text='The payment request this batch came '
                                     'from. One request can produce several '
                                     'batches (one per line under Individual '
                                     'processing).',
                       )
    # Set when this batch is a payroll salary run (payroll→FNB auto-load),
    # so a period is never loaded twice (double-pay guard) and the batch is
    # traceable back to the period. Null for ordinary vendor/EFT batches.
    payroll_period   = models.ForeignKey(
                           'payroll.PayrollPeriod', null=True, blank=True,
                           on_delete=models.SET_NULL,
                           related_name='fnb_batches',
                       )

    class Meta(BaseModel.Meta):
        verbose_name        = 'FNB Batch Submission'
        verbose_name_plural = 'FNB Batch Submissions'
        ordering            = ['-created_at']

    def __str__(self):
        return (f'{self.idempotency_key} — {self.payment_count} payments, '
                f'BWP {self.total_amount_bwp} ({self.status})')


# ---------------------------------------------------------------------------
# FNBWebhookEvent — inbound notifications from FNB
# ---------------------------------------------------------------------------

class FNBWebhookEvent(BaseModel):
    """Every webhook from FNB lands here first. We verify the signature,
    store the raw payload, then dispatch to the right handler.
    """

    class Status(models.TextChoices):
        RECEIVED   = 'received',   'Received'
        VERIFIED   = 'verified',   'Signature verified'
        PROCESSED  = 'processed',  'Processed'
        REJECTED   = 'rejected',   'Rejected (signature fail)'
        FAILED     = 'failed',     'Processing failed'

    event_type      = models.CharField(max_length=80, blank=True, default='',
                                       help_text='As reported by FNB '
                                                 '(payment.acknowledged, '
                                                 'statement.ready, etc.)')
    external_id     = models.CharField(max_length=128, blank=True, default='',
                                       help_text='FNB-assigned event ID for '
                                                 'idempotent replay.')
    received_at     = models.DateTimeField(auto_now_add=True)
    raw_payload     = models.JSONField(default=dict, blank=True)
    raw_headers     = models.JSONField(default=dict, blank=True)
    signature       = models.CharField(max_length=300, blank=True, default='')

    status          = models.CharField(max_length=12, choices=Status.choices,
                                       default=Status.RECEIVED)
    processed_at    = models.DateTimeField(null=True, blank=True)
    error_message   = models.TextField(blank=True, default='')

    # Optional link back to whatever this event resolved
    related_batch   = models.ForeignKey(
                          'fnb.FNBBatchSubmission', null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='webhook_events',
                      )

    class Meta(BaseModel.Meta):
        verbose_name        = 'FNB Webhook Event'
        verbose_name_plural = 'FNB Webhook Events'
        ordering            = ['-received_at']
        constraints = [
            models.UniqueConstraint(
                fields=['external_id'],
                condition=models.Q(external_id__gt=''),
                name='uq_fnb_webhook_external_id',
            ),
        ]

    def __str__(self):
        return f'{self.event_type} {self.external_id} ({self.status})'


# ---------------------------------------------------------------------------
# FnbCredential — the live FNB API login, entered by the CFO in-app
# ---------------------------------------------------------------------------
import base64
import hashlib
from cryptography.fernet import Fernet
from django.conf import settings as _settings


def _fnb_fernet() -> Fernet:
    """Fernet built from the Django SECRET_KEY — the FNB client secret is stored
    encrypted at rest, so it is never persisted or logged in clear."""
    key = base64.urlsafe_b64encode(hashlib.sha256(_settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


class FnbCredential(BaseModel):
    """The live FNB API login (Client ID + secret), set by the CFO via the in-app
    Bank Connection page so the bank login can be entered/replaced WITHOUT anyone
    touching the server. Singleton (one row). The secret is Fernet-encrypted and
    is NEVER returned by any API; the client decrypts it only at call time. When
    no row exists the integration falls back to the environment settings."""

    client_id         = models.CharField(max_length=128, blank=True, default='')
    client_secret_enc = models.TextField(blank=True, default='',
                                         help_text='Fernet-encrypted; never exposed.')
    updated_by        = models.ForeignKey(
                            User, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name='fnb_credentials_updated',
                        )

    class Meta(BaseModel.Meta):
        verbose_name        = 'FNB Credential'
        verbose_name_plural = 'FNB Credentials'

    def __str__(self):
        return f'FNB credential ({self.client_id or "unset"})'

    @classmethod
    def load(cls):
        """The single live credential row, or None."""
        return cls.objects.order_by('-created_at').first()

    def set_secret(self, raw: str) -> None:
        self.client_secret_enc = _fnb_fernet().encrypt(raw.encode()).decode() if raw else ''

    def get_secret(self) -> str:
        if not self.client_secret_enc:
            return ''
        try:
            return _fnb_fernet().decrypt(self.client_secret_enc.encode()).decode()
        except Exception:  # noqa: BLE001
            return ''

    @property
    def has_secret(self) -> bool:
        return bool(self.client_secret_enc)


# ---------------------------------------------------------------------------
# FNBHealthAlertState — singleton state for the "FNB is down" watcher
# ---------------------------------------------------------------------------

class FNBHealthAlertState(BaseModel):
    """Remembers whether the FNB link was last seen UP or DOWN, so the watcher
    only emails on a state CHANGE (up->down, down->up) instead of every run.
    Singleton — one row, loaded via .load()."""

    class State(models.TextChoices):
        UNKNOWN = 'unknown', 'Unknown'
        UP      = 'up',      'Up'
        DOWN    = 'down',    'Down'

    state           = models.CharField(max_length=10, choices=State.choices,
                                       default=State.UNKNOWN)
    changed_at      = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    detail          = models.CharField(max_length=300, blank=True, default='')

    # Stuck-batch alerting (2026-08-20) rides on the same singleton row but
    # keeps its own dedupe state, so the up/down state machine above is
    # untouched. stuck_keys is the newline-separated set of batch keys the
    # last alert covered — keying on the SET (not merely "we emailed
    # recently") is what lets a NEWLY stuck batch through the quiet window.
    stuck_keys        = models.TextField(blank=True, default='')
    stuck_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'FNB Health Alert State'
        verbose_name_plural = 'FNB Health Alert State'

    def __str__(self):
        return f'FNB health = {self.state}'

    @classmethod
    def load(cls):
        row = cls.objects.order_by('created_at').first()
        return row or cls.objects.create(state=cls.State.UNKNOWN)


# ---------------------------------------------------------------------------
# Proof of payment capture (CFO 2026-08-26)
#
# FNB emails a per-payment result to the CFO mailbox for every OnceOff payment.
# Those emails ARE the only proof we keep — measured on a month of real mail,
# NONE of them carries an attachment, so the body text is the record.
#
# Measured on the same month before building this: 48 payments, BWP 2,585,588.37.
# Only 3 matched an Omni payment request; 15 of 17 carrying a claim number
# matched a real claim. So this files against the CLAIM first, the payment
# request when one exists, and puts the rest in a queue — because that queue is
# itself the finding: money leaving the bank with no record in Omni.
# ---------------------------------------------------------------------------

class FNBProofOfPayment(BaseModel):
    """One FNB payment-result email, kept as the proof, filed where it belongs.

    Keyed on the Graph message id so a re-run over an overlapping window can
    never file the same proof twice.
    """

    class State(models.TextChoices):
        FILED_CLAIM   = 'filed_claim',   'Filed against a claim'
        FILED_REQUEST = 'filed_request', 'Filed against a payment request'
        UNFILED       = 'unfiled',       'Unfiled — no record in Omni'
        PROPOSED      = 'proposed',      'Match proposed, awaiting confirmation'
        DISMISSED     = 'dismissed',     'Reviewed, deliberately left unfiled'

    # --- the email ------------------------------------------------------- #
    graph_message_id = models.CharField(max_length=512, unique=True)
    mailbox          = models.CharField(max_length=200)
    received_at      = models.DateTimeField(db_index=True)
    subject          = models.CharField(max_length=300, blank=True, default='')
    body_text        = models.TextField(
                           help_text="The email exactly as FNB sent it. This IS the proof.")

    # --- what FNB said --------------------------------------------------- #
    reference   = models.CharField(max_length=200, db_index=True)
    amount      = models.DecimalField(max_digits=18, decimal_places=2, db_index=True)
    bank_status = models.CharField(max_length=60, blank=True, default='')
    paid        = models.BooleanField(default=False, db_index=True)

    # --- the stored proof ------------------------------------------------ #
    proof_pdf = models.FileField(upload_to='fnb-pop/%Y/%m/', blank=True, null=True)

    # --- where it was filed ---------------------------------------------- #
    state = models.CharField(max_length=16, choices=State.choices,
                             default=State.UNFILED, db_index=True)
    claim = models.ForeignKey('integrations.GraphiteClaim', on_delete=models.SET_NULL,
                              null=True, blank=True, related_name='fnb_proofs')
    payment_request = models.ForeignKey('taskboard.PaymentRequest',
                                        on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='fnb_proofs')
    claim_number_seen = models.CharField(
                            max_length=64, blank=True, default='', db_index=True,
                            help_text='The claim number read out of the FNB reference, '
                                      'even when no claim row matched it.')

    # --- AI proposal (CFO 2026-08-26: propose only, a human confirms) ----- #
    # The CFO's own 2026-08-20 rule is "AI explains, never validates money", so
    # nothing here files itself. The deterministic match files; the AI only ever
    # writes a SUGGESTION into these fields for a person to accept or reject.
    proposed_kind    = models.CharField(max_length=16, blank=True, default='',
                                        help_text="'claim' or 'payment_request'")
    proposed_ref     = models.CharField(max_length=120, blank=True, default='')
    proposed_reason  = models.TextField(blank=True, default='')
    proposed_at      = models.DateTimeField(null=True, blank=True)
    proposal_engine  = models.CharField(max_length=40, blank=True, default='')

    confirmed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name='confirmed_fnb_proofs')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    review_note  = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['state', '-received_at']),
            models.Index(fields=['paid', 'state']),
        ]

    def __str__(self) -> str:
        return f'{self.reference} BWP {self.amount:,.2f} ({self.get_state_display()})'

    @property
    def is_filed(self) -> bool:
        return self.state in (self.State.FILED_CLAIM, self.State.FILED_REQUEST)

    @property
    def needs_a_human(self) -> bool:
        """In the queue the CFO gets weekly: paid money with nowhere to file it."""
        return self.paid and self.state in (self.State.UNFILED, self.State.PROPOSED)


# ---------------------------------------------------------------------------
# Express Pay — saved payees (CFO 2026-09-04: "a place where frequently paid
# payments, where I can load people's details so I can click and pay")
# ---------------------------------------------------------------------------
class ExpressPayee(BaseModel):
    """A person or supplier the CFO/CEO pays often, saved once so the phone
    can fill the Express Pay form with one tap. Bank details only — no money,
    no approval state. Shared between the two people who may use Express Pay
    (core.permissions.is_cfo_or_ceo); nobody else can read or write it.
    Soft-deleted (is_active) so a removed payee's past loads still make sense."""
    name           = models.CharField(max_length=140)
    bank_name      = models.CharField(max_length=120, default='FNB')
    account_number = models.CharField(max_length=32)
    branch_code    = models.CharField(max_length=16, blank=True, default='')
    default_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    reference      = models.CharField(max_length=120, blank=True, default='')
    company_code   = models.CharField(max_length=12, default='ADIC')
    category       = models.CharField(max_length=16, default='operations')   # 'operations' | 'claim'
    note           = models.CharField(max_length=200, blank=True, default='')
    is_active      = models.BooleanField(default=True)
    created_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='express_payees')
    last_paid_at   = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['name']
        constraints = [
            # "Re-saving never duplicates" is a promise the database keeps, not
            # just the view: one saved payee per account number (soft-deleted
            # rows are re-activated by the view rather than re-created).
            models.UniqueConstraint(fields=['account_number'], name='uniq_expresspayee_account'),
        ]

    def __str__(self) -> str:
        return f'{self.name} ({self.bank_name} ···{self.account_number[-4:]})'
