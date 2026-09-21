"""
procurement/kyc_service.py

Payment-side KYC guard.

`check_kyc_for_payment(payment)` is called at the top of Payment.confirm().
It blocks confirmation when the counterparty's KYC record is expired AND
the payment is over the configured BWP threshold.

Threshold: settings.KYC_BLOCK_THRESHOLD_BWP (default 50,000).

Other states (missing, flagged) are deliberately NOT hard-blocked here:
  - 'missing' is handled by the vendor-onboarding workflow upstream.
  - 'flagged' is handled by the compliance dashboard out-of-band.

This service ONLY enforces the expiry + threshold rule the CFO asked for.
Keeping the scope tight means the guard never blocks a legitimate small
payment to a vendor whose annual KYC re-screen slipped a few days.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError


DEFAULT_KYC_BLOCK_THRESHOLD_BWP = Decimal('50000')


def _threshold() -> Decimal:
    """Return the BWP threshold above which expired KYC blocks a payment."""
    raw = getattr(settings, 'KYC_BLOCK_THRESHOLD_BWP', DEFAULT_KYC_BLOCK_THRESHOLD_BWP)
    if isinstance(raw, Decimal):
        return raw
    return Decimal(str(raw))


def check_kyc_for_payment(payment) -> None:
    """Raise ValidationError if the counterparty KYC blocks this payment.

    Rule (CFO directive):
        Block when ``payment.contact.kyc_status == 'expired'``
        AND      ``payment.amount > settings.KYC_BLOCK_THRESHOLD_BWP``.

    All other KYC states (missing / flagged / ok) pass through here — they
    are surfaced elsewhere in the workflow but do not hard-block confirm.
    """
    contact = getattr(payment, 'contact', None)
    if contact is None:
        return  # nothing to check — defensive

    status = getattr(contact, 'kyc_status', None)
    if status != 'expired':
        return

    amount = payment.amount or Decimal('0')
    if amount <= _threshold():
        return

    raise ValidationError(
        f"KYC for {contact.name} is expired. Payments above "
        f"BWP {_threshold():,.2f} require an in-date KYC record. "
        "Renew the counterparty's KYC (TIN / COI / bank letter / sanctions "
        "re-screen) before confirming this payment."
    )
