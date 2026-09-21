"""Supplier Payables Reconciliation — enumerations and tunables.

Kept out of models.py (reconciliation_hub convention) so services, serializers
and tests can import choice values without pulling the model layer in.

Ageing buckets deliberately mirror ``reporting`` AP-aging so the two surfaces
never disagree: current / 31-60 / 61-90 / 91-120 / over-120, where "current"
means not yet 31 days past due (includes not-yet-due).
"""
from decimal import Decimal

from django.db import models

ZERO = Decimal('0.00')

# One-thebe tolerance, matching payments.PaymentAllocation's overpayment guard.
CENT = Decimal('0.01')


class SupplierCategory(models.TextChoices):
    """Why we are buying from this supplier. Drives the claim-authorisation rule."""
    PANEL_BEATER = 'panel_beater', 'Panel Beater'
    PARTS        = 'parts',        'Parts Supplier'
    TOWING       = 'towing',       'Towing / Recovery'
    ASSESSOR     = 'assessor',     'Assessor'
    GLASS        = 'glass',        'Glass Fitment'
    MEDICAL      = 'medical',      'Medical Provider'
    # Claim-backed but the exact trade is not yet confirmed. This is what
    # classify_recon_suppliers assigns to a vendor whose POs are mostly raised
    # by Claims. Deliberately NOT `OTHER`: a human picking "Other" for a
    # stationery supplier must not inherit the claim-authorisation rule.
    CLAIMS_OTHER = 'claims_other', 'Claims Supplier (trade unconfirmed)'
    GENERAL      = 'general',      'General / Operations'
    OTHER        = 'other',        'Other'


# Motor-repair and claim-supplier spend: a bill in these categories must tie
# back to a claim authorisation (procurement.PurchaseOrder.related_claim_reference).
# Mirrors procurement.po_classification's 'claims' purchase type.
CLAIM_BACKED_CATEGORIES = frozenset({
    SupplierCategory.PANEL_BEATER,
    SupplierCategory.PARTS,
    SupplierCategory.TOWING,
    SupplierCategory.ASSESSOR,
    SupplierCategory.GLASS,
    SupplierCategory.MEDICAL,
    # Fable 5 review 2026-07-25: this member was missing, which left the whole
    # claim-authorisation gate INERT on prod. classify_recon_suppliers seeds
    # claims-heavy vendors into this category, so with it excluded `no_claim`
    # could never fire and the CFO's "No claim authority" tile was a guaranteed
    # zero across all 31 claim suppliers. Any change here needs a test pinning
    # the constant, not just the happy-path category.
    SupplierCategory.CLAIMS_OTHER,
})


class RunStatus(models.TextChoices):
    OPEN        = 'open',        'Open'
    IN_PROGRESS = 'in_progress', 'In Progress'
    FINALISED   = 'finalised',   'Finalised'
    REOPENED    = 'reopened',    'Reopened'


class LineStatus(models.TextChoices):
    PENDING   = 'pending',   'Pending Action'
    EXCEPTION = 'exception', 'Exception'
    CLEARED   = 'cleared',   'Cleared'


class MatchStatus(models.TextChoices):
    MATCHED   = 'matched',   '3-Way Matched'
    PARTIAL   = 'partial',   'Partially Matched'
    NO_PO     = 'no_po',     'No PO'
    NO_GRN    = 'no_grn',    'No Goods Receipt'
    NO_CLAIM  = 'no_claim',  'No Claim Authorisation'
    UNMATCHED = 'unmatched', 'Unmatched'


class PaymentStatus(models.TextChoices):
    PAID           = 'paid',           'Paid'
    PARTIALLY_PAID = 'partially_paid', 'Partially Paid'
    UNPAID         = 'unpaid',         'Unpaid'
    HELD           = 'held',           'Held'


# Anything other than PAID must carry a reason code + written justification.
NOT_FULLY_PAID = frozenset({
    PaymentStatus.UNPAID,
    PaymentStatus.PARTIALLY_PAID,
    PaymentStatus.HELD,
})


class LedgerStage(models.TextChoices):
    """Whether the bill has actually reached the general ledger.

    Discovered on the first prod build (2026-07-25): every vendor bill in omni
    is still ``draft`` — raised, PO-matched, but never posted. A draft bill is
    NOT in the GL and NOT in the AP Aging report, yet it is unmistakably an
    unpaid supplier obligation that somebody must account for. Leaving those off
    the board would have shown the CFO an empty page and implied nothing was
    owed, which is the opposite of the truth.

    So drafts are included and flagged. Totals are reported split by stage so
    the posted subset still ties to AP Aging exactly.

    Determined by whether the bill has a ``journal_entry``, NOT by its status:
    ``payments._update_invoice_from_allocations`` rewrites status to PAID by
    direct DB update when an allocation confirms, even on a never-posted bill,
    so status can say "paid" with no journal behind it.
    """
    POSTED = 'posted', 'Posted to the ledger'
    DRAFT  = 'draft',  'Not yet posted'


class EscalationStatus(models.TextChoices):
    OPEN         = 'open',         'Open'
    ACKNOWLEDGED = 'acknowledged', 'Acknowledged'
    RESOLVED     = 'resolved',     'Resolved'
    WAIVED       = 'waived',       'Waived'


# Live escalations — a run cannot finalise while an escalation-worthy item has
# none of these on record.
LIVE_ESCALATION_STATUSES = frozenset({
    EscalationStatus.OPEN,
    EscalationStatus.ACKNOWLEDGED,
})


class ReasonGroup(models.TextChoices):
    DISPUTE            = 'dispute',             'Query / Dispute'
    DOCS_MISSING       = 'docs_missing',        'Missing Documents'
    SALVAGE_NOT_IN_YARD = 'salvage_not_in_yard', 'Salvage Not Yet in Yard'
    AWAITING_APPROVAL  = 'awaiting_approval',   'Awaiting Approval'
    NO_PO              = 'no_po',               'No Purchase Order'
    FUNDS              = 'funds',               'Funding / Cash-flow Hold'
    DUPLICATE          = 'duplicate',           'Suspected Duplicate'
    FX                 = 'fx',                  'FX / Rate Confirmation'
    OTHER              = 'other',               'Other'


# Ageing buckets, keyed to match reporting's AP-aging keys exactly.
# (lower_days_inclusive, upper_days_inclusive_or_None) measured as days PAST DUE.
AGEING_BUCKETS = (
    ('current',    None, 30),
    ('days_31_60',   31, 60),
    ('days_61_90',   61, 90),
    ('days_91_120',  91, 120),
    ('over_120',    121, None),
)

# Minimum characters of written justification. Matches the taskboard
# completion-note floor so payables cannot type "n/a".
MIN_JUSTIFICATION_CHARS = 20

# ---------------------------------------------------------------------------
# Supplier statement ingestion + statement-to-ledger matching
# ---------------------------------------------------------------------------
# The "front half" of the module (usability test 2026-08-24): a supplier sends
# their own statement of account; Finance uploads it and Omni reconciles the
# statement against the vendor bills / payments it already holds, then proposes
# what to pay. This app still moves no money — it produces a recommendation a
# human acts on in payments + FNB.

class StatementStatus(models.TextChoices):
    UPLOADED = 'uploaded', 'Uploaded'
    PARSED   = 'parsed',   'Parsed'
    MATCHED  = 'matched',  'Matched'
    ERROR    = 'error',    'Error'


class StatementLineType(models.TextChoices):
    """What one row of a supplier statement represents."""
    INVOICE     = 'invoice',     'Invoice / Charge'
    CREDIT_NOTE = 'credit_note', 'Credit Note'
    PAYMENT     = 'payment',     'Payment / Receipt'
    BALANCE_FWD = 'balance_fwd', 'Balance Brought Forward'
    OTHER       = 'other',       'Other / Unclassified'


class StmtMatchType(models.TextChoices):
    """Outcome of reconciling one statement line (or one Omni bill) — worst-last
    so a sort by value surfaces the clean matches first and the exceptions after."""
    MATCHED          = 'matched',          'Matched'
    AMOUNT_VARIANCE  = 'amount_variance',  'Amount Variance'
    CREDIT_NOTE      = 'credit_note',      'Credit Note'
    ALREADY_PAID     = 'already_paid',     'Already Paid in Omni'
    STATEMENT_ONLY   = 'statement_only',   'On Statement, not in Omni'
    OMNI_ONLY        = 'omni_only',        'In Omni, not on Statement'
    DUPLICATE        = 'duplicate',        'Duplicate on Statement'
    UNMATCHED_PAYMENT = 'unmatched_payment', 'Statement Payment, no Omni match'


class PaymentProposal(models.TextChoices):
    """What Omni recommends for a statement/ledger line. A recommendation only —
    the CFO authorises the actual payment in FNB."""
    PAY_NOW      = 'pay_now',     'Pay now'
    HOLD         = 'hold',        'Hold'
    DO_NOT_PAY   = 'do_not_pay',  'Do not pay'
    INVESTIGATE  = 'investigate', 'Investigate'


# A statement line amount within this absolute tolerance of the Omni bill is a
# clean match; beyond it is an AMOUNT_VARIANCE the human must investigate. One
# thebe mirrors the payments overpayment guard (CENT above).
STATEMENT_AMOUNT_TOLERANCE = CENT

# Match types that must NEVER be auto-proposed for payment — the safety rule
# from the usability test: never recommend paying an item that is unmatched,
# duplicated, unapproved or already paid. Enforced in statement_matching. This
# frozenset IS the rule, so it must list every non-payable type; a test pins its
# exact membership (see L2 — a gate keyed off a set that silently drops a member).
NEVER_PAY_MATCH_TYPES = frozenset({
    StmtMatchType.STATEMENT_ONLY,
    StmtMatchType.DUPLICATE,
    StmtMatchType.ALREADY_PAID,
    StmtMatchType.UNMATCHED_PAYMENT,
    StmtMatchType.CREDIT_NOTE,
    StmtMatchType.OMNI_ONLY,
})

# Days past due at which an unpaid bill becomes seriously overdue: it is shown
# in strong red, payables MUST record a reason, and it auto-escalates to the
# board owner (Bharath's feedback 2026-07-27, points 2/3/5). A bill only 1–29
# days past due is still shown red but does not force an escalation, and a bill
# not yet due is shown green — so a payment due next month no longer looks like
# a problem. Measured as days past the bill's due date (issue date + the
# supplier's agreed payment terms).
OVERDUE_ESCALATION_DAYS = 30
