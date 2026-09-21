"""genric/constants.py — the GENRIC treaty constants, in ONE place.

Source: Alpha_Direct_GENRIC_Reporting_Build_Prompt.docx (Finance, 12 Sep 2026),
"Fixed rules — hard-code these ... set by the signed treaty and company records.
Do not change them without written instruction."

Entity: Alpha Direct South Africa (Third Party Motor). Money is ZAR (R), which
is why the VAT rate here is 15% and NOT Botswana's 14% — do not reach for
``settings.RC_VAT_RATE`` in this app, it is a different country's tax.

Everything that is a treaty number lives here as a Decimal. Nothing in this app
may inline a rate; a treaty change must be a one-line edit in this file plus a
test that moves.
"""
from __future__ import annotations

from decimal import Decimal

# ── The one number the whole pack turns on ──────────────────────────────────
#: Ceding commission GENRIC pays back on ceded premium.
#: The older MOU said 10% — the signed treaty wins.
#: (Finance, 12 Sep 2026: "If any code or document says 10%, it is wrong.")
CEDING_COMMISSION_RATE = Decimal('0.215')

# ── The rest of the treaty / company record ────────────────────────────────
#: Standard premium per policy per month, VAT inclusive.
STANDARD_PREMIUM_INCL_VAT = Decimal('99.00')

#: South African VAT. Used only to split incl/excl VAT on collected premium.
SA_VAT_RATE = Decimal('0.15')

#: Share of the net premium base passed to GENRIC.
QUOTA_SHARE_CEDED = Decimal('0.90')

#: Share Alpha Direct keeps. Stated as its own constant rather than derived, so
#: a treaty that ever stops the two summing to 1 is caught by the test below
#: rather than silently producing a quota share that does not reconcile.
RETENTION = Decimal('0.10')

#: The reinsurance premium invoice is cross-border and therefore zero-rated.
#: Invoice VAT is R0.00 — this is not "VAT we forgot", it is the treaty.
REINSURANCE_VAT_RATE = Decimal('0.00')

#: Grace before a policy may be cancelled — treaty Art. 10.4.
CANCELLATION_GRACE_MONTHS = 2

#: Policies older than this are flagged — treaty Appendix 1/3. Needs the
#: Graphite ``createdAt`` / inception date on every policy row.
POLICY_AGE_FLAG_MONTHS = 18

# ── Company records ────────────────────────────────────────────────────────
#: The ONE account a GENRIC premium may land in. A policy counts only when the
#: money actually arrives here — the book is bank-anchored, not Graphite-anchored.
FNB_COLLECTION_ACCOUNT = '63104367974'

#: GENRIC's own bank details are NOT hard-coded here and must never be guessed.
#: They come from the GENRIC_REINSURER_BANK_DETAILS setting — see genric/config.

#: Invoice numbering. July 2026 was 024; the next number is confirmed by Finance
#: per run, never guessed — see genric.invoice.
INVOICE_PREFIX = 'GENRIC-1-'
INVOICE_NUMBER_WIDTH = 3

#: Reporting entity, as it must print on every report and the invoice.
ENTITY_NAME = 'Alpha Direct South Africa'
ENTITY_BOOK = 'Third Party Motor'

#: Currency symbol for this book. ZAR, not Pula.
CURRENCY = 'R'

#: Which way the net reinsurance premium travels. CFO ruling, 13 Sep 2026:
#: this is a quota-share cession, so the cedant pays the reinsurer — Alpha
#: Direct pays GENRIC, never the other way round. The first build printed
#: "PAYABLE TO" over Alpha Direct's own FNB account; that was inverted.
PAYMENT_DIRECTION = (
    f'{ENTITY_NAME} (cedant) pays GENRIC (reinsurer). Quota-share cession — '
    f'the cedant remits the net reinsurance premium.'
)


def assert_treaty_self_consistent() -> None:
    """Cheap guard: ceded + retained must be the whole book.

    Called from the cession calculation. If somebody edits one of the two
    shares and not the other, the pack must refuse to produce an invoice
    rather than bill GENRIC for a share that does not exist.
    """
    total = QUOTA_SHARE_CEDED + RETENTION
    if total != Decimal('1'):
        raise ValueError(
            f'GENRIC treaty shares do not sum to 100%: ceded '
            f'{QUOTA_SHARE_CEDED} + retention {RETENTION} = {total}. '
            f'Fix genric/constants.py before generating a pack.'
        )
