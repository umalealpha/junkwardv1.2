"""large_payments — the CEO authorisation request for large claim payments.

CFO 2026-09-11, in his words: "currently we make this request manually … the team
loads the payment into omni, kago approves it and it goes to fnb automatically,
and after it reaches fnb we manually copy the payments into outlook and request
for payments. So its cumbersome. So i want a button, request large claim payment."

WHAT THIS MODULE IS, AND WHAT IT IS DELIBERATELY NOT.

It is a READER and a PACKAGER. It selects payment requests that have ALREADY been
through Omni's own gated payment path — raised, finance-signed-off, loaded to FNB —
and assembles them into one authorisation request for the CEO. It creates no
payment, amends no payment, releases no money, and changes nothing on
taskboard.PaymentRequest. The bulk-upload lesson of 2026-09-11 applies here
unchanged: Omni's payment create path carries ~600 lines of money controls, and a
second code path that wrote payments would walk past all of them. This module has
no write access to any of that — it holds its own rows and points AT the payment
requests by foreign key.

CFO decision 2026-09-11 (asked and answered before any code was written):
  * the request is raised AFTER the payment has reached FNB, as the manual process
    does today. The CEO's approval is therefore a RECORD, not a release gate —
    this is stated plainly rather than implied, because a control that looks like
    a gate and is not one is worse than no control.
  * the default threshold is BWP 20,000 (the 1-Sep run's cut-off). It is a
    setting, not a constant in code, so it changes without a rebuild.
  * Finance approvers and the CFO may raise one. It still lands on the CFO for
    approval before it can go anywhere.

WHY THE LINE FIELDS ARE A SNAPSHOT AND NEVER READ LIVE.
Everything on LargePaymentLine — the amount, the payee, the insured name, the loss
description — is copied in ONCE, at the moment the request is raised, and never
re-read. An authorisation document must say the same thing tomorrow as it said
when it was approved. If the underlying payment request is later amended, this
record still shows what the CFO actually approved.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import BaseModel

#: Fallback when LARGE_PAYMENT_THRESHOLD_BWP is not set. The 1-Sep-2026 cut-off.
DEFAULT_THRESHOLD_BWP = Decimal('20000.00')


def default_threshold() -> Decimal:
    """The 'large' cut-off, from settings, never hard-coded at the call site."""
    raw = getattr(settings, 'LARGE_PAYMENT_THRESHOLD_BWP', None)
    if raw in (None, ''):
        return DEFAULT_THRESHOLD_BWP
    try:
        return Decimal(str(raw))
    except Exception:
        return DEFAULT_THRESHOLD_BWP


class LargePaymentRequest(BaseModel):
    """One authorisation request covering several large claim payments."""

    class Status(models.TextChoices):
        # The Graphite pull is running. The UI polls progress_pct while this
        # lasts — the CFO asked for "10% done, 20% done, 60% done" rather than a
        # frozen screen, because the claim detail genuinely takes time to fetch.
        ENRICHING    = 'enriching',    'Collecting claim details'
        # The pull could not run at all (Graphite unreachable / misconfigured).
        # The request is NOT lost and NOT silently empty — it says so, and it
        # can be retried. A failed enrichment that looked like an empty result
        # is exactly how a payment request would go out with blank insured names.
        ENRICH_FAILED = 'enrich_failed', 'Could not collect claim details'
        PENDING_CFO  = 'pending_cfo',  'Pending CFO approval'
        APPROVED     = 'approved',     'Approved by the CFO'
        REJECTED     = 'rejected',     'Rejected by the CFO'
        # ── Piece 2, the CEO leg (CFO 2026-09-12) ────────────────────────────
        # The CFO's approval now SENDS. These four are the CEO's side.
        SENT_TO_CEO  = 'sent_to_ceo',  'With the CEO for authorisation'
        QUESTION     = 'question',     'CEO has asked a question'
        CEO_APPROVED = 'ceo_approved', 'Authorised by the CEO'
        CEO_REJECTED = 'ceo_rejected', 'Refused by the CEO'
        CANCELLED    = 'cancelled',    'Cancelled'

    #: Statuses that no longer hold any of their payments. A payment on a
    #: cancelled or refused request is free to be picked up on a new one — and
    #: that MUST include the CEO's refusal, or a payment Arun sent back could
    #: never be put to him again on a corrected request.
    RELEASING_STATUSES = ('cancelled', 'rejected', 'ceo_rejected')

    ref          = models.CharField(max_length=48, unique=True)
    title        = models.CharField(
                       max_length=200, blank=True, default='',
                       help_text='What this run is called, e.g. "Claims Payments '
                                 '10 September 2026".')
    raised_by    = models.ForeignKey(settings.AUTH_USER_MODEL,
                                     on_delete=models.PROTECT,
                                     related_name='large_payment_requests_raised')
    status       = models.CharField(max_length=20, choices=Status.choices,
                                    default=Status.ENRICHING, db_index=True)
    #: The cut-off in force when this request was raised, kept on the record so a
    #: later settings change cannot rewrite what "large" meant that day.
    threshold    = models.DecimalField(max_digits=16, decimal_places=2,
                                       default=DEFAULT_THRESHOLD_BWP)
    total        = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # ── Progress, so the screen is never a frozen box ────────────────────────
    progress_pct  = models.PositiveSmallIntegerField(default=0)
    progress_note = models.CharField(max_length=160, blank=True, default='')
    enrich_error  = models.TextField(blank=True, default='')

    # ── The CFO's decision ───────────────────────────────────────────────────
    cfo_task      = models.ForeignKey('core.OmniTask', null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='large_payment_requests')
    decided_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                      blank=True, on_delete=models.SET_NULL,
                                      related_name='large_payment_requests_decided')
    decided_at    = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default='')

    # ── The CEO leg (Piece 2, CFO 2026-09-12) ────────────────────────────────
    # "Arun won't use omni" — his words. So the EMAIL IS THE WHOLE INTERFACE:
    # every button on it works with no login, and nothing here may depend on him
    # opening Omni. His task below exists as a RECORD and to drive the chase, not
    # as the way he acts.
    ceo_task      = models.ForeignKey('core.OmniTask', null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='large_payment_ceo_requests')
    sent_to_ceo_at   = models.DateTimeField(null=True, blank=True)
    #: Exactly who the authorisation actually went to, read back from the send.
    #: aiyer@ is on Omni's NEVER_CC list and is stripped unless the sender is
    #: told otherwise — on 2026-08-10 a send reported success with Arun simply
    #: not on the message. So the recipients are RECORDED from the result rather
    #: than assumed from the intent.
    sent_recipients  = models.JSONField(default=list, blank=True)
    send_error       = models.TextField(blank=True, default='')
    ceo_decided_at   = models.DateTimeField(null=True, blank=True)
    ceo_decision_note = models.TextField(blank=True, default='')
    #: What the CEO asked, and of whom. Appended to, never overwritten — a second
    #: question must not erase the first.
    ceo_questions    = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Large payment authorisation request'

    def __str__(self) -> str:
        return f'{self.ref} ({self.get_status_display()})'

    @property
    def flagged_count(self) -> int:
        return sum(1 for ln in self.lines.all() if ln.flags)


class LargePaymentLine(models.Model):
    """One payment on the request, plus the claim detail pulled from Graphite.

    Deliberately NOT a BaseModel subclass in behaviour terms: these rows are a
    frozen snapshot and are never edited after the enrichment finishes.
    """

    class EnrichStatus(models.TextChoices):
        PENDING   = 'pending',   'Not collected yet'
        OK        = 'ok',        'Found in Graphite'
        NOT_FOUND = 'not_found', 'No such claim in Graphite'
        NO_CLAIM  = 'no_claim',  'No claim number on the payment'
        ERROR     = 'error',     'Lookup failed'

    id      = models.BigAutoField(primary_key=True)
    request = models.ForeignKey(LargePaymentRequest, on_delete=models.CASCADE,
                                related_name='lines')
    #: PROTECT, not CASCADE: an approved authorisation must not lose its lines
    #: because someone deleted the underlying payment request afterwards.
    payment_request = models.ForeignKey('taskboard.PaymentRequest',
                                        on_delete=models.PROTECT,
                                        related_name='large_payment_lines')

    # ── Snapshot taken from the payment request at selection time ────────────
    payment_ref  = models.CharField(max_length=48, blank=True, default='')
    payee        = models.CharField(max_length=200, blank=True, default='')
    amount       = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    claim_number = models.CharField(max_length=40, blank=True, default='',
                                    db_index=True)

    # ── Pulled from Graphite, read-only ──────────────────────────────────────
    enrich_status   = models.CharField(max_length=12, choices=EnrichStatus.choices,
                                       default=EnrichStatus.PENDING)
    insured_name    = models.CharField(max_length=200, blank=True, default='')
    policy_number   = models.CharField(max_length=60, blank=True, default='')
    claim_status    = models.CharField(max_length=60, blank=True, default='')
    claim_sub_status = models.CharField(max_length=60, blank=True, default='')
    date_of_loss    = models.DateField(null=True, blank=True)
    loss_description = models.TextField(blank=True, default='')
    reserve_amount  = models.DecimalField(max_digits=16, decimal_places=2,
                                          null=True, blank=True)
    paid_amount     = models.DecimalField(max_digits=16, decimal_places=2,
                                          null=True, blank=True)

    #: Things a human must look at before this is approved — e.g. the claim is
    #: not in Graphite, the insured name came back as a placeholder, the claim is
    #: closed. A list of {code, message}. Never a silent pass.
    flags = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-amount']
        constraints = [
            # One payment appears at most ONCE on a given request. The whole
            # point of this module is to stop a payment being requested twice,
            # so it must not be able to be requested twice inside one request
            # either.
            models.UniqueConstraint(fields=['request', 'payment_request'],
                                    name='uniq_large_payment_line_per_request'),
        ]

    def __str__(self) -> str:
        return f'{self.claim_number or self.payment_ref} — {self.amount}'
