"""claims_rules/engine.py — the five claims/premium automation rule sets, as a
pure decision engine.

Source of the rules: Lindani Mababa (Assistant Accountant) spec to the CFO,
2026-09-08 — five rule sets covering write-offs, premium confirmations, AOL
deductions, recoveries, and the Real Pay collections monitor. The wording in
every message below is HIS wording; it is what the claims clerk, the finance
clerk and the retention agent actually read on screen, so do not "improve" a
string here without the spec changing first.

WHY THIS IS PURE PYTHON. Nothing in this module touches the database, the ORM,
the network or the clock. Every function takes plain facts (counts, booleans,
Decimals, names) and returns a list of `Outcome` objects. That is deliberate:
the rules are the part that must be provably right, and a rule you can only
exercise through a live policy record is a rule nobody re-tests. The real data
adapters — read a policy, count active items, look up the last successful
Real Pay debit — get wired on top of this later and can be swapped without
touching a single decision.

DESIGN RULE FROM THE CFO — settled 2026-08-11, not up for re-litigation:
    green auto-releases with no human in the loop; RED NEVER AUTO-DECLINES.
A 'hold' produced here means "stop, a person must look at this" and nothing
more. It is never a repudiation, never a decline, and never a rejection of a
claim. That is why no `kind` in this module is 'decline' and why no message
below contains the words decline, repudiate or reject — a machine may pause a
claim, only management may refuse one. Rule set 5 comes closest to that line
(three consecutive unpaid premiums is the existing repudiation-qualifying
threshold) and it still only WARNS.

MONEY. Decimal + ROUND_HALF_UP everywhere, never float. Rounding is a tax
decision in this company, not a language default (VAT rounds half up, house
rule). Amounts shown to a human carry a thousands comma and 2 decimals —
`1,234.00` — because that is the format on every other Omni money screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

TWO = Decimal('0.01')
ZERO = Decimal('0.00')

#: Billing frequencies the engine knows. Anything else is a caller bug, not a
#: business case — an unrecognised frequency must not silently pick a branch.
MONTHLY = 'monthly'
QUARTERLY = 'quarterly'
ANNUAL = 'annual'


@dataclass(frozen=True)
class Outcome:
    """One thing the system should do, or one thing a human should be told.

    kind      — 'notify' | 'task' | 'hold' | 'release' | 'prompt' | 'upload_required'
    audience  — 'claims' | 'finance' | 'underwriting' | 'operations' | 'retention' | 'technical'
    message   — the spec wording, with values substituted. Shown as-is.
    severity  — 'info' | 'warning' | 'urgent' | 'critical'
    """

    kind: str
    audience: str
    message: str
    severity: str


# ----------------------------------------------------------------- helpers --

def _dec(raw) -> Decimal:
    """A money input as a Decimal, HALF UP to 2dp.

    Accepts Decimal/int/str. Goes through str() for anything that is not
    already a Decimal so a float that sneaks in from a caller is read at its
    printed value rather than its binary approximation — 0.1 must not become
    0.1000000000000000055511151231257827.
    """
    d = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    return d.quantize(TWO, rounding=ROUND_HALF_UP)


def _money(raw) -> str:
    """A money value the way every other Omni screen shows it: `1,234.00`."""
    return f'{_dec(raw):,.2f}'


def _check_frequency(billing_frequency: str, allowed: tuple) -> str:
    freq = (billing_frequency or '').strip().lower()
    if freq not in allowed:
        raise ValueError(
            f'unknown billing_frequency {billing_frequency!r}; expected one of {allowed}'
        )
    return freq


# =============================================================== RULE SET 1 ==
# WRITE-OFFS. A vehicle's status becomes "Written Off" (total loss) and two
# quite different things follow depending on whether that vehicle WAS the
# policy or was one item on it.

def prorata_premium_adjustment(
    annual_or_period_premium,
    days_remaining: int,
    days_in_period: int,
) -> Decimal:
    """Premium attributable to the unexpired part of the period, HALF UP to 2dp.

    Used when a written-off item leaves the policy part-way through a billing
    period: the customer keeps cover on the rest of the fleet for the days that
    remain, so the amended premium is a day-count share of the period premium.

    days_remaining <= 0 → 0.00 (nothing left to charge). days_remaining above
    days_in_period is clamped to the full period premium — a pro-rata share can
    never exceed the premium it is a share of, and letting a bad day-count
    inflate a debit order is exactly the kind of silent over-charge we do not
    want reaching Real Pay.
    """
    if days_in_period <= 0:
        # Not a business case: a period with no days is a caller bug. Fail loud
        # rather than divide by zero or invent a number.
        raise ValueError('days_in_period must be greater than 0')
    if days_remaining <= 0:
        return ZERO
    if days_remaining >= days_in_period:
        return _dec(annual_or_period_premium)
    premium = _dec(annual_or_period_premium)
    share = premium * Decimal(days_remaining) / Decimal(days_in_period)
    return share.quantize(TWO, rounding=ROUND_HALF_UP)


def evaluate_write_off(
    client_name: str,
    vehicle_label: str,
    active_item_count: int,
    amended_premium=None,
) -> List[Outcome]:
    """Outcomes for a vehicle marked "Written Off".

    active_item_count is the TOTAL active insured items on that client, the
    written-off vehicle included — so 1 means this write-off empties the policy.
    """
    if active_item_count <= 0:
        # Data error, not a business branch. The write-off cannot be actioned
        # because there is nothing on the policy to action it against. Never
        # crash the claims screen over it — tell technical and stop.
        return [Outcome(
            kind='notify',
            audience='technical',
            message=(
                f'DATA ERROR: Client {client_name} has no active insured items on the '
                f'system, so the write-off of Vehicle {vehicle_label} cannot be '
                f'actioned. Check the client record before proceeding.'
            ),
            severity='warning',
        )]

    if active_item_count == 1:
        # The written-off vehicle IS the policy. Nothing is left to insure, so
        # the policy closes and — the part that costs real money if missed —
        # the debit order stops.
        return [
            Outcome(
                kind='notify',
                audience='operations',
                message=(
                    f'CRITICAL: Client {client_name} has only one vehicle on the system, '
                    f'which has been marked as a write-off.'
                ),
                severity='critical',
            ),
            Outcome(
                kind='notify',
                audience='claims',
                message=(
                    f'CRITICAL: Client {client_name} has only one vehicle on the system, '
                    f'which has been marked as a write-off.'
                ),
                severity='critical',
            ),
            Outcome(
                kind='task',
                audience='finance',
                message=(
                    f'POLICY CANCELLATION: Please completely cancel the policy and stop '
                    f'all future debit orders on the Real Pay system immediately for '
                    f'Client {client_name}.'
                ),
                severity='urgent',
            ),
        ]

    # active_item_count > 1 — the policy survives, one item leaves it, and the
    # premium comes down. Two owners: underwriting takes the item off, finance
    # re-points Real Pay at the new amount.
    outcomes = [Outcome(
        kind='task',
        audience='underwriting',
        message=(
            f'ACTION REQUIRED: Please remove Vehicle {vehicle_label} from Client '
            f'{client_name}\'s active policy due to a write-off.'
        ),
        severity='urgent',
    )]

    if amended_premium is None:
        # Engine-added guard, not in the spec: the spec's premium message names
        # an amount, and we will not print a placeholder into an instruction
        # that changes a debit order. No amount supplied → say so, do not guess.
        outcomes.append(Outcome(
            kind='notify',
            audience='technical',
            message=(
                f'PREMIUM ADJUSTMENT PENDING: Vehicle {vehicle_label} removal for Client '
                f'{client_name} has no amended premium supplied, so the Real Pay system '
                f'cannot be updated yet.'
            ),
            severity='warning',
        ))
        return outcomes

    outcomes.append(Outcome(
        kind='notify',
        audience='finance',
        message=(
            f'PREMIUM ADJUSTMENT: Vehicle removed. Please update Real Pay system with '
            f'the newly amended premium of {_money(amended_premium)} for the next debit '
            f'cycle.'
        ),
        severity='warning',
    ))
    return outcomes


# =============================================================== RULE SET 2 ==
# PREMIUM CONFIRMATIONS. Before a claim proceeds, the billing behind it must be
# both GENERATED and PAID. Those are two different failures with two different
# owners, and the spec is emphatic that they are not merged.

def evaluate_premium_confirmation(
    policy_id: str,
    client_name: str,
    billing_frequency: str,
    expected_invoice_count: int,
    generated_invoice_count: int,
    unpaid_or_failed_count: int,
) -> List[Outcome]:
    """Authorise, or hold, a claim on the strength of the billing behind it.

    Note what this function does NOT do: it never changes the policy status.
    Branch A is an authorisation to the claims clerk, not a state change on the
    policy — the spec says so explicitly.
    """
    _check_frequency(billing_frequency, (MONTHLY, QUARTERLY, ANNUAL))
    if expected_invoice_count < 1:
        # A billing cycle that expects no invoice cannot be "up to date", and
        # we are not auto-releasing a claim off an empty expectation.
        raise ValueError('expected_invoice_count must be at least 1')
    if generated_invoice_count < 0 or unpaid_or_failed_count < 0:
        raise ValueError('invoice counts cannot be negative')

    missing_invoice = generated_invoice_count < expected_invoice_count
    unpaid = unpaid_or_failed_count > 0

    # Branch A — everything generated, everything paid. The only green path.
    if not missing_invoice and not unpaid:
        return [Outcome(
            kind='release',
            audience='claims',
            message=(
                f'AUTHORIZATION GRANTED: Go ahead and proceed with the claim for Policy '
                f'{policy_id}. All invoices are up to date and premiums are paid.'
            ),
            severity='info',
        )]

    outcomes: List[Outcome] = []

    # ORDERING CHOICE, and it matters. When an invoice was never generated AND
    # something is unpaid, both are true and both are reported — but B goes
    # FIRST. An ungenerated invoice is OUR system's fault, not the customer's,
    # and if C were read first the clerk would chase a customer for a premium
    # we never billed. Nothing is dropped: B's outcomes are returned ahead of
    # C's, never instead of them.
    if missing_invoice:
        outcomes.append(Outcome(
            kind='hold',
            audience='claims',
            message=(
                'CLAIM ON HOLD: Do not proceed. System Invoicing Error detected. The '
                'required billing invoice was not generated by the system.'
            ),
            severity='urgent',
        ))
        outcomes.append(Outcome(
            kind='notify',
            audience='finance',
            message=(
                f'URGENT FINANCE CHECK: System Invoicing Error on Policy {policy_id} for '
                f'Client {client_name}. The expected invoice was not generated. Please '
                f'investigate and rectify the premium status.'
            ),
            severity='urgent',
        ))

    if unpaid:
        outcomes.append(Outcome(
            kind='hold',
            audience='claims',
            message=(
                'CLAIM ON HOLD: Do not proceed. Premium payment is missing or outstanding '
                'for the current billing cycle.'
            ),
            severity='urgent',
        ))
        outcomes.append(Outcome(
            kind='notify',
            audience='finance',
            message=(
                f'FINANCE CHECK REQUIRED: Claim is on hold for Policy {policy_id} (Client: '
                f'{client_name}) due to outstanding premiums. Please check the premium '
                f'status and payment records.'
            ),
            severity='urgent',
        ))

    return outcomes


# =============================================================== RULE SET 3 ==
# AOL DEDUCTIONS — the amount outstanding between the last successful payment
# and the renewal month, to be deducted from a settlement.

@dataclass(frozen=True)
class AolResult:
    """The AOL figure and the numbers it was built from.

    requires_user_confirmation is TRUE and stays true. The spec requires a
    person to press "Confirm Calculation" before any deduction is taken off a
    settlement, so this engine only ever produces a figure FOR REVIEW. It has no
    way to record a deduction as applied, and it must not grow one — a machine
    reducing what a claimant is paid, with no human signature on the number, is
    not a road this company goes down.
    """

    total: Decimal
    billing_frequency: str
    gap_months: int
    periods_owed: int
    requires_user_confirmation: bool = True


def calculate_aol(item_premium, billing_frequency: str, gap_months: int) -> AolResult:
    """Outstanding amount owed at claim time, HALF UP to 2dp.

    monthly   — one premium per unpaid month.
    quarterly — one premium per unpaid QUARTER, and any fraction of a quarter
                owes the whole quarter (Lindani Mababa spec 2026-09-08: the
                quarter is the billable unit, so a 4-month gap is 2 quarters,
                not 1.33). ceil() by integer arithmetic, never float.
    """
    freq = _check_frequency(billing_frequency, (MONTHLY, QUARTERLY))
    if gap_months <= 0:
        # Paid up to renewal: nothing outstanding, nothing to deduct.
        return AolResult(total=ZERO, billing_frequency=freq, gap_months=gap_months,
                         periods_owed=0)

    if freq == MONTHLY:
        periods = gap_months
    else:
        periods = -(-gap_months // 3)   # integer ceil(gap_months / 3)

    total = (_dec(item_premium) * Decimal(periods)).quantize(TWO, rounding=ROUND_HALF_UP)
    return AolResult(total=total, billing_frequency=freq, gap_months=gap_months,
                     periods_owed=periods)


def aol_breakdown_lines(item_premium, billing_frequency: str, gap_months: int) -> List[str]:
    """The AOL review block, exactly as the spec lays it out on screen.

    Returned as lines rather than one blob so the screen can render it as a list
    and a task/email can join it with newlines.
    """
    result = calculate_aol(item_premium, billing_frequency, gap_months)
    if result.billing_frequency == MONTHLY:
        freq_label = 'Monthly'
        unit = 'month' if result.periods_owed == 1 else 'months'
    else:
        freq_label = 'Quarterly'
        unit = 'quarter' if result.periods_owed == 1 else 'quarters'
    return [
        'AOL CALCULATION FOR REVIEW:',
        f'- Billing Frequency: {freq_label}',
        f'- Item Premium: {_money(item_premium)}',
        f'- Unpaid Period: {result.gap_months} Months (From Last Payment to Renewal Month)',
        f'- Calculated Quarters/Months Owed: {result.periods_owed} {unit}',
        f'- SYSTEM CALCULATED AOL TO DEDUCT: {_money(result.total)}',
    ]


def evaluate_aol(item_premium, billing_frequency: str, gap_months: int) -> List[Outcome]:
    """Outcomes for the AOL check on a claim."""
    result = calculate_aol(item_premium, billing_frequency, gap_months)

    if result.total == ZERO:
        return [Outcome(
            kind='notify',
            audience='claims',
            message=(
                f'AOL CHECK PASSED: Outstanding amount is {_money(ZERO)}. Proceed with '
                f'claim.'
            ),
            severity='info',
        )]

    message = (
        f'AOL DEDUCTION REQUIRED: Full outstanding amount of {_money(result.total)} '
        f'detected. Hold claim execution until calculation is verified by user and '
        f'processed for deduction.'
    )
    # Hold first (it is the instruction), then the same wording to both desks —
    # finance takes the deduction, claims holds the settlement.
    return [
        Outcome(kind='hold', audience='claims', message=message, severity='urgent'),
        Outcome(kind='notify', audience='finance', message=message, severity='urgent'),
        Outcome(kind='notify', audience='claims', message=message, severity='urgent'),
    ]


# =============================================================== RULE SET 4 ==
# RECOVERIES. Where there is someone to recover from, the Notice of Demand is
# the document that makes the recovery possible — and it has to be on file
# before the claim moves, not after.

def evaluate_recovery(
    third_party_at_fault: bool,
    other_recovery_grounds: bool,
    notice_of_demand_attached: bool,
) -> List[Outcome]:
    """Gate claim progress on the Notice of Demand where recovery is possible.

    No recovery ground → the NOD check is bypassed entirely (an empty list, not
    a silent pass-with-warning). Nothing to recover, nothing to demand.
    """
    if not (third_party_at_fault or other_recovery_grounds):
        return []

    if notice_of_demand_attached:
        # Spec left this to us ("empty list or a single info"). We return the
        # info line: the claims file should show the NOD was checked and found,
        # not just show nothing.
        return [Outcome(
            kind='notify',
            audience='claims',
            message=(
                'NOTICE OF DEMAND ON FILE: Recovery grounds flagged and the Notice of '
                'Demand (NOD) is attached. No hold on claim progress.'
            ),
            severity='info',
        )]

    return [
        Outcome(
            kind='prompt',
            audience='claims',
            message=(
                'POSSIBLE RECOVERY DETECTED: This claim has been flagged for potential '
                'recovery options. ACTION REQUIRED: Please attach the Notice of Demand '
                '(NOD) immediately to proceed.'
            ),
            severity='urgent',
        ),
        # The field label itself — the upload control the clerk sees.
        Outcome(
            kind='upload_required',
            audience='claims',
            message='Upload Notice of Demand Here',
            severity='urgent',
        ),
        Outcome(
            kind='hold',
            audience='claims',
            message=(
                'POSSIBLE RECOVERY DETECTED: This claim has been flagged for potential '
                'recovery options. ACTION REQUIRED: Please attach the Notice of Demand '
                '(NOD) immediately to proceed.'
            ),
            severity='urgent',
        ),
    ]


# =============================================================== RULE SET 5 ==
# REAL PAY MONITOR. Two separate watches: money that moved on Real Pay but
# never landed in Graphite, and a collection cycle drifting towards a lapse.

#: Three consecutive unpaid premiums is the existing repudiation-qualifying
#: threshold in the policy wording. The engine only WARNS at it — whether a
#: contract lapses or a claim is refused is management's decision, never this
#: module's (see the CFO design rule in the module docstring).
DEFAULT_MAX_ALLOWED_FAILURES = 3


def evaluate_realpay_transaction(
    policy_id: str,
    processed_on_realpay: bool,
    posted_to_graphite: bool,
) -> List[Outcome]:
    """Catch a Real Pay collection that never registered against the policy.

    This is the reconciliation break that makes a paid-up customer look unpaid,
    which is how a good policy ends up flagged for lapse. Technical, urgent,
    same day.
    """
    if processed_on_realpay and not posted_to_graphite:
        return [Outcome(
            kind='notify',
            audience='technical',
            message=(
                f'GRAPHITE INTEGRATION ERROR: A payment transaction has been processed on '
                f'Real Pay for Policy {policy_id} but is NOT REGISTERED TO GRAPHITE. '
                f'Immediate technical reconciliation required.'
            ),
            severity='urgent',
        )]
    return []


def evaluate_realpay_collection_cycle(
    policy_id: str,
    client_name: str,
    weekly_debit_failed: bool,
    cumulative_failures: int,
    graphite_payment_found_this_cycle: bool,
    max_allowed_failures: int = DEFAULT_MAX_ALLOWED_FAILURES,
) -> List[Outcome]:
    """Alert on a failed weekly debit, and escalate a policy heading for lapse.

    The two checks are independent: a single failed debit is an alert to
    operations; the failure count at or over the threshold, OR a cycle with no
    Graphite payment at all, is a retention escalation. Both can fire together.
    """
    outcomes: List[Outcome] = []

    if weekly_debit_failed:
        outcomes.append(Outcome(
            kind='notify',
            audience='operations',
            message=(
                f'ALERT: Weekly premium payment via Real Pay has failed for Policy '
                f'{policy_id} (Client: {client_name}). Check payment alignment.'
            ),
            severity='urgent',
        ))

    if cumulative_failures >= max_allowed_failures or not graphite_payment_found_this_cycle:
        lapse_message = (
            f'URGENT NOTICE: Policy contract for {client_name} (Policy {policy_id}) is '
            f'ABOUT TO LAPSE due to failed Real Pay collections and missing updates in '
            f'Graphite. Immediate retention intervention required.'
        )
        # Same wording to both desks: operations owns the collection, retention
        # owns the customer.
        outcomes.append(Outcome(kind='notify', audience='operations',
                                message=lapse_message, severity='critical'))
        outcomes.append(Outcome(kind='notify', audience='retention',
                                message=lapse_message, severity='critical'))

    return outcomes
