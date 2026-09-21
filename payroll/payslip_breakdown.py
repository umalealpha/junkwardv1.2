"""
payroll/payslip_breakdown.py — the ONE authoritative way to split a payslip's
lines into Earnings / Deductions / Employer contributions.

Why this module exists (CFO audit 2026-07-25). The split was duplicated in
`payroll/pdf.py` and `core/staff_mobile_views.py`, and BOTH copies used:

    if kind in ('deduction', 'tax', 'employee_tax') or amt < 0:  ...deduction
    else:                                                        ...earning

That test is wrong three ways:

1. The real `PayslipComponent.Kind` values are `employee_deduction` and
   `employee_pretax` — neither matches `'deduction'`. They only landed in the
   Deductions column because their amounts happen to be stored NEGATIVE. A
   deduction ever stored positive printed as an EARNING and inflated the slip.
2. `company_contribution` fell through to the `else`, so employer medical +
   provident printed as though the EMPLOYEE earned them. On 66 of 102 July-2026
   slips the earnings column therefore did not sum to the printed Gross — on the
   worst of them the listed earnings overstated Gross by more than 20,000.
3. A NEGATIVE earning (the BURS §32 housing salary sacrifice, stored as
   e.g. HOUSING_ALLOWANCE −14,000) was caught by `amt < 0` and printed as a
   14,000 Deduction — but it had already reduced Gross, so the slip showed
   14,000 being deducted that was not deducted.

The worst live case combined (2) and (3) with a header-only PAYE: one employee's
printed slip would have shown a net roughly BWP 19.5k HIGHER than payroll
actually paid, because the rows came from the lines while Gross and Net were
printed from the stored totals with nothing comparing the two. Hence
`Breakdown.variances` — a payslip must never print two numbers that disagree
without saying so.

Sign convention on the stored data (verified on prod 2026-07-25): deduction and
pretax lines are stored NEGATIVE; earnings positive except a salary-sacrifice
reduction which is a negative EARNING. Take magnitudes for display; never let a
negative flip a column.

The arithmetic here is the same as `Payslip.recompute_totals` and the /Payroll
skill §2:

    gross  = Σ earnings (a negative earning reduces gross ONCE)
    net    = gross − PAYE − |deductions| − |pretax|
    ctc    = gross + Σ employer contributions
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from decimal import Decimal

from .models import PayslipComponent

log = logging.getLogger(__name__)

ZERO = Decimal('0.00')
K = PayslipComponent.Kind

# Kinds that are the employee's own money coming off the payslip. PRETAX is a
# real deduction from the employee's pocket (pension/provident) even though it
# also reduces taxable pay — it must appear in Deductions, never in Earnings.
_DEDUCTION_KINDS = (K.EMPLOYEE_DEDUCTION, K.EMPLOYEE_PRETAX)
_EARNING_KINDS = (K.EARNING, K.EARNING_NON_TAXABLE)

# Kinds that deliberately carry NO money onto the payslip. These are the
# derived-total marker kinds; a line of one of these is a label, not a figure.
#
# Named explicitly, and pinned by a test, because of the L2 failure class: a
# gate keyed off a category set silently drops a member nobody noticed was
# missing. If a future Kind carries employee money and lands in neither the
# earning, deduction, tax, employer nor ignored set, it would be dropped by
# `build_breakdown` AND by `Payslip.recompute_totals` — so stored would equal
# derived, no variance would fire, and the money would be invisible on the
# payslip. `test_every_component_kind_is_accounted_for` fails the build if the
# enum ever grows without this module being updated.
_IGNORED_COMPUTED_KINDS = (K.COMPUTED_GROSS, K.COMPUTED_NET, K.COMPUTED_CTC)

# Every Kind must fall into exactly one of these buckets.
_CLASSIFIED_KINDS = frozenset(
    _EARNING_KINDS + _DEDUCTION_KINDS + _IGNORED_COMPUTED_KINDS
    + (K.TAX, K.COMPANY_CONTRIBUTION)
)


@dataclass
class Breakdown:
    """A payslip split into the three blocks a reader must be able to add up."""

    earnings: list[tuple[str, Decimal]] = field(default_factory=list)
    deductions: list[tuple[str, Decimal]] = field(default_factory=list)
    employer: list[tuple[str, Decimal]] = field(default_factory=list)

    gross: Decimal = ZERO
    paye: Decimal = ZERO
    total_deductions: Decimal = ZERO   # includes PAYE
    net: Decimal = ZERO
    employer_total: Decimal = ZERO
    ctc: Decimal = ZERO
    taxable: Decimal = ZERO

    # Set when the figures derived from the lines disagree with the totals
    # stored on the Payslip row. Callers MUST surface this rather than print
    # two unrelated numbers side by side (see the module docstring — one live
    # July-2026 slip itemised a net ~19.5k above what payroll actually paid).
    variances: list[str] = field(default_factory=list)

    # Same check, for figures the employee no longer SEES. Cost-to-company was
    # removed from the payslip (CFO / Pako Kago 2026-07-29), so a CTC mismatch
    # must not appear in the employee's "under review" band — warning someone
    # about a number that is not on their document is noise. The tie-out itself
    # still runs: a broken CTC means payroll data drifted, which finance must be
    # able to see. Reconciliation kept, display removed.
    internal_variances: list[str] = field(default_factory=list)

    @property
    def ties(self) -> bool:
        """True when both columns add up, which is the whole point."""
        return (
            sum((a for _, a in self.earnings), ZERO) == self.gross
            and self.gross - self.total_deductions == self.net
        )


def build_breakdown(payslip, *, check_stored: bool = True) -> Breakdown:
    """Split `payslip` into Earnings / Deductions / Employer contributions.

    Amounts in the returned lists are display magnitudes (always positive),
    EXCEPT a salary-sacrifice reduction, which stays negative in `earnings` so
    the column visibly sums to Gross.

    `check_stored=True` compares the line-derived totals against the totals
    stored on the Payslip row and records any difference in `variances`.
    """
    b = Breakdown()

    lines = sorted(
        payslip.lines.all(),
        key=lambda l: (getattr(l.component, 'sort_order', 0) or 0),
    )

    for ln in lines:
        comp = ln.component
        amt = ln.amount or ZERO
        label = (getattr(comp, 'name', '') or '').strip() or '—'
        kind = getattr(comp, 'kind', '') or ''

        if kind == K.TAX:
            b.paye += abs(amt)
            continue

        if kind == K.COMPANY_CONTRIBUTION:
            if amt:
                b.employer.append((label, abs(amt)))
                b.employer_total += abs(amt)
            continue

        if kind in _DEDUCTION_KINDS:
            if amt:
                b.deductions.append((label, abs(amt)))
                b.total_deductions += abs(amt)
            if kind == K.EMPLOYEE_PRETAX:
                b.taxable -= abs(amt)
            continue

        if kind in _EARNING_KINDS:
            # A negative earning is a salary sacrifice: it reduces gross once
            # and belongs in the Earnings column with its sign, NOT in
            # Deductions (where it would read as a second, phantom deduction).
            b.earnings.append((label, amt))
            b.gross += amt
            if kind == K.EARNING:
                b.taxable += amt
            continue

        # Unknown / computed kinds carry no money onto the slip.

    # Headline-only BWP slip: payroll holds Gross / PAYE / Net on the header but
    # no earning lines. Without this the slip printed Gross 0.00 and a red
    # "under review" band, which staff cannot hand to a bank (CFO 2026-09-18).
    # Show the stored gross as one line; the variance check below still runs.
    # Foreign headline-only slips are handled in pdf.generate_payslip_pdf from
    # source_gross, so only a slip positively in BWP takes this path.
    headline_only = False
    if not b.earnings and (getattr(payslip, 'source_currency', 'BWP') or 'BWP') == 'BWP':
        stored_gross = getattr(payslip, 'gross_amount', None)
        if stored_gross:
            b.earnings.append(('Gross Salary', Decimal(str(stored_gross))))
            b.gross = Decimal(str(stored_gross))
            headline_only = not b.deductions

    # PAYE may live only on the header (`paye_amount`) with no PayslipLine —
    # true for every roll-forward slip. Show it once, from whichever source has
    # it, and only add it to Deductions after the loop so ordering is stable.
    #
    # On a foreign-currency slip the fallback MUST come from `source_paye`:
    # `paye_amount` is the BWP reporting figure, and mixing it into a column of
    # INR lines would produce an INR-minus-BWP net with the variance check
    # skipped, so nothing would catch it (Fable review 2026-07-25, fix 4).
    if not b.paye:
        if getattr(payslip, 'is_foreign_currency', False):
            fallback = getattr(payslip, 'source_paye', None)
        else:
            fallback = getattr(payslip, 'paye_amount', None)
        if fallback:
            b.paye = abs(fallback)
    if b.paye:
        b.deductions.append(('PAYE', b.paye))
        b.total_deductions += b.paye

    # A headline-only slip's stored net is usually already net of medical /
    # pension that never came across as lines. Same rule as the foreign path
    # in pdf.py: show the gap as one balancing deduction so the slip adds up.
    # Only a POSITIVE gap; net above gross-less-PAYE stays a visible variance.
    if headline_only and getattr(payslip, 'net_amount', None) is not None:
        other = b.gross - b.total_deductions - Decimal(str(payslip.net_amount))
        if other > 0:
            b.deductions.append(('Other deductions (per payroll)', other))
            b.total_deductions += other

    b.net = b.gross - b.total_deductions
    b.ctc = b.gross + b.employer_total

    if check_stored:
        _compare_stored(b, payslip)
    return b


def _compare_stored(b: Breakdown, payslip) -> None:
    """Record any disagreement between the lines and the stored totals.

    Foreign-currency slips are skipped: their stored *_amount fields are the
    BWP reporting figures while the lines are in the source currency, so a
    difference there is by design (CFO 2026-06-20).
    """
    if getattr(payslip, 'is_foreign_currency', False):
        return
    # Gross and Net are ON the payslip, so a mismatch there is shown to the
    # reader. Cost-to-company is NOT on the payslip any more (CFO / Pako Kago
    # 2026-07-29) — its tie-out still runs, but into `internal_variances` so
    # finance can see data drift without warning an employee about a figure
    # their document no longer shows.
    for label, derived, stored, employee_facing in (
        ('Gross', b.gross, getattr(payslip, 'gross_amount', None), True),
        ('Net', b.net, getattr(payslip, 'net_amount', None), True),
        ('Cost to company', b.ctc, getattr(payslip, 'ctc_amount', None), False),
    ):
        if stored is None:
            continue
        if Decimal(str(stored)) != derived:
            msg = (
                f'{label}: this payslip itemises {derived:,.2f} but payroll '
                f'holds {Decimal(str(stored)):,.2f}'
            )
            if employee_facing:
                b.variances.append(msg)
            else:
                # Not on the employee's document, so it cannot be shown there —
                # but a check nobody can observe is not a check. Log it so
                # finance/ops see payroll data drift.
                b.internal_variances.append(msg)
                log.warning('payslip %s internal variance — %s',
                            getattr(payslip, 'pk', '?'), msg)
