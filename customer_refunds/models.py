"""
customer_refunds/models.py

The Omni end of the UniCoin / Alpha Direct MIS customer-refund process.

The dashboard (intake → AI green light → Motlatsi/Bharath approval) lives in
Graphite. Once a refund is green-lit and approved THERE, Graphite hands it to
Omni. Omni owns the money leg:

    RECEIVED → FINANCE_QUEUE → APPROVED → FNB_LOADED → PAID → POSTED_BACK
                                      ↘ REJECTED

Finance (one of Keetile / Pako / Tlamelo / Kago) approves, which raises a
one-off `payments.Payment`; that payment is loaded into an FNB EFT batch
(GATED — preview-only until REFUND_FNB_SEND_ENABLED + Finance sign-off);
on PAID we post the refund back onto the Graphite policy.

PII rule (AD-POL-AI-GOV-001): the customer bank account number is stored
ENCRYPTED at rest here (core.vault_crypto / Fernet). Only the last four digits
are kept in clear for display, and it is decrypted only in-process to build the
FNB payment. NOTE: once the payment is raised, the number lands in clear in
payments.Payment.payee_account_number — the inherited once-off-payment pattern
(a known platform-wide M4 item, not new to this module). Names / policy numbers
stay server-side; never logged un-anonymised; API responses show last-4 only.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel
from core.vault_crypto import decrypt, encrypt

ZERO = Decimal('0.00')


class CustomerRefund(AuditableMixin, BaseModel):
    """One MIS customer refund, received from Graphite, paid via FNB."""

    class Segment(models.TextChoices):
        # Kept separate — different teams manage each (CFO 2026-07-24).
        MIS        = 'mis',        'MIS / UniCoin (micro-insurance)'
        DOMESTIC   = 'domestic',   'Domestic (personal lines)'
        COMMERCIAL = 'commercial', 'Commercial (business lines)'

    class Status(models.TextChoices):
        RECEIVED      = 'received',      'Received from Graphite'
        FINANCE_QUEUE = 'finance_queue', 'With Finance'
        APPROVED      = 'approved',      'Finance approved'
        FNB_LOADED    = 'fnb_loaded',    'Loaded to FNB'
        PAID          = 'paid',          'Paid'
        POSTED_BACK   = 'posted_back',   'Posted back to policy'
        REJECTED      = 'rejected',      'Rejected'

    # ── Segment (MIS / Domestic / Commercial — managed by different teams) ───
    segment       = models.CharField(
                        max_length=12, choices=Segment.choices,
                        default=Segment.MIS, db_index=True)

    # ── Provenance (from Graphite) ──────────────────────────────────────────
    graphite_ref  = models.CharField(
                        max_length=64, unique=True, db_index=True,
                        help_text='Graphite refund_requests id — idempotency key.')
    policy_number = models.CharField(max_length=64, db_index=True)
    product_name  = models.CharField(max_length=191, blank=True, default='')
    customer_name = models.CharField(max_length=191, blank=True, default='')
    agent_name    = models.CharField(max_length=191, blank=True, default='')
    reason        = models.CharField(max_length=191, blank=True, default='')

    # ── Money ───────────────────────────────────────────────────────────────
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    currency      = models.CharField(max_length=3, default='BWP')

    # ── Bank (PII — account number encrypted at rest) ────────────────────────
    bank_name          = models.CharField(max_length=120, blank=True, default='')
    branch_name        = models.CharField(max_length=120, blank=True, default='')
    branch_code        = models.CharField(max_length=20, blank=True, default='')
    account_number_enc = models.TextField(
                            blank=True, default='',
                            help_text='Fernet-encrypted customer account number.')
    account_last4      = models.CharField(max_length=4, blank=True, default='')
    # Blind index: keyed HMAC of the account number. Fernet uses a random IV so
    # ciphertext can't be matched — this deterministic fingerprint lets us detect
    # the SAME account across refunds (fraud) WITHOUT storing the number in clear.
    account_fingerprint = models.CharField(max_length=64, blank=True, default='',
                                           db_index=True)

    # ── AI green light (done in Graphite; carried for the audit trail) ───────
    ai_greenlight = models.BooleanField(default=False)
    ai_evidence   = models.JSONField(default=dict, blank=True)

    # ── Fraud review (computed on intake + re-runnable) ──────────────────────
    fraud_flags     = models.JSONField(default=list, blank=True,
                        help_text='List of {severity, code, detail} fraud signals.')
    fraud_score     = models.IntegerField(default=0, db_index=True,
                        help_text='0 clean · higher = more/severe signals.')
    fraud_reviewed_at = models.DateTimeField(null=True, blank=True)
    # Optional DeepSeek (reasoning_complete) advisory opinion over the rule-based
    # signals — PII-free input only. {ok, risk, reasons[], recommend, engine}.
    ai_fraud_review   = models.JSONField(default=dict, blank=True)

    # ── Workflow ─────────────────────────────────────────────────────────────
    status             = models.CharField(
                            max_length=16, choices=Status.choices,
                            default=Status.RECEIVED, db_index=True)
    finance_approved_by = models.ForeignKey(
                            User, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name='customer_refunds_approved')
    finance_approved_at = models.DateTimeField(null=True, blank=True)
    reject_reason       = models.TextField(blank=True, default='')

    # ── Links out ────────────────────────────────────────────────────────────
    payment    = models.ForeignKey(
                    'payments.Payment', null=True, blank=True,
                    on_delete=models.SET_NULL, related_name='customer_refunds')
    fnb_batch  = models.ForeignKey(
                    'fnb.FNBBatchSubmission', null=True, blank=True,
                    on_delete=models.SET_NULL, related_name='customer_refunds')
    paid_at         = models.DateTimeField(null=True, blank=True)
    graphite_posted = models.BooleanField(default=False)
    graphite_posted_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Customer Refund'
        verbose_name_plural = 'Customer Refunds'

    def __str__(self):
        return f'{self.policy_number} · {self.refund_amount} {self.currency} · {self.status}'

    # ── PII helpers ──────────────────────────────────────────────────────────
    def set_account_number(self, raw: str) -> None:
        """Encrypt + stash the account number; keep last-4 + a blind-index
        fingerprint (for fraud matching) but never the number in clear."""
        raw = (raw or '').strip()
        self.account_number_enc = encrypt(raw) if raw else ''
        self.account_last4 = raw[-4:] if len(raw) >= 4 else raw
        self.account_fingerprint = account_fingerprint(raw) if raw else ''

    def get_account_number(self) -> str:
        """Decrypt the account number (in-process use only — never log)."""
        return decrypt(self.account_number_enc)


def account_fingerprint(raw: str) -> str:
    """Deterministic keyed HMAC of a bank account number — a blind index.
    Same account → same fingerprint (so fraud matching works) but the number is
    not recoverable from it. Digits only, so formatting differences don't hide a
    match. Key: settings.REFUND_ACCOUNT_INDEX_KEY, else derived from SECRET_KEY.
    """
    import hashlib
    import hmac as _hmac

    from django.conf import settings as _s

    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    # Leading zeros are formatting, not identity. Graphite strips them before
    # hashing because 0621... and 621... were fingerprinting as two different
    # accounts while the bank paid one (Pramod Bisen, 2026-09-11). Omni has to
    # canonicalise identically or the cross-system same-account check silently
    # matches nothing on any account ever written with a leading zero.
    digits = digits.lstrip('0')
    if not digits:
        return ''
    key = (getattr(_s, 'REFUND_ACCOUNT_INDEX_KEY', '') or _s.SECRET_KEY).encode()
    return _hmac.new(key, digits.encode(), hashlib.sha256).hexdigest()
