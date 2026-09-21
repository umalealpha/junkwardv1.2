"""genric/reports.py — the regulatory pack, one builder per report.

Thirteen reports, built from two sources and nothing typed: the bank
(``banking.BankStatementLine``, classified in ``collections.py``) and the
policy book (``policies.py``).

Four report states, and the difference between the last two is the whole
point of this build:

    ok             produced from data
    nil_no_activity  produced, and genuinely empty — there was nothing to report
    nil_no_source    NOT produced: no source system feeds this report yet
    blocked          NOT produced: a required input or decision is missing

The old manual process reported the cancellations as nil. It was not nil — 71
policies were candidates and R7,029 a month was at risk. "Do not write nil
without checking" is in the build prompt, so an empty report may only be called
nil when a source was actually read and came back empty. A report with no
source says so, loudly, and never renders as a clean nil return.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Sequence

from . import constants as K
from . import policies as P
from .config import (
    REMIT_TO_QUESTION,
    UNPAID_DAYS_KEY,
    UNPAID_DAYS_QUESTION,
    is_remit_to_configured,
    is_unpaid_days_configured,
    unpaid_days_before_cancellation,
)
from .money import q2

OK = 'ok'
NIL_NO_ACTIVITY = 'nil_no_activity'
NIL_NO_SOURCE = 'nil_no_source'
BLOCKED = 'blocked'


@dataclass
class Report:
    key: str
    title: str
    status: str
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    kpis: list = field(default_factory=list)     # (label, value) pairs, already formatted
    notes: list = field(default_factory=list)
    nmi: str = ''
    reconciled_against: str = ''                  # what every figure here was checked to

    def as_dict(self) -> dict:
        return {
            'key': self.key,
            'title': self.title,
            'nmi': self.nmi,
            'status': self.status,
            'row_count': len(self.rows),
            'columns': self.columns,
            'kpis': [[a, b] for a, b in self.kpis],
            'notes': self.notes,
            'reconciled_against': self.reconciled_against,
        }


@dataclass
class PackContext:
    """Everything the builders read. Assembled once, in services.build_pack()."""
    year: int
    month: int
    classified: list                      # ClassifiedLine
    summary: Any                          # CollectionSummary
    cession: Any                          # Cession
    book: Any                             # BookPosition
    current_policies: list                # PolicyRow
    prior_policies: list                  # PolicyRow — may be empty
    has_prior_export: bool
    invoice_number: str = ''
    invoice_needs_confirmation: bool = False
    policy_source: str = ''
    bank_statement_label: str = ''

    @property
    def period_end(self) -> date:
        return P.month_end(self.year, self.month)

    @property
    def period_label(self) -> str:
        return f'{calendar.month_name[self.month]} {self.year}'


def _m(v) -> str:
    from .money import money
    return money(v, K.CURRENCY)


# ── 1. GWP Report (NMI 10.2.1) ──────────────────────────────────────────────
def build_gwp(ctx: PackContext) -> Report:
    s = ctx.summary
    rows = []
    for channel in ('realpay', 'payat', 'eft'):
        slot = s.by_channel.get(channel)
        if not slot:
            continue
        rows.append([
            {'realpay': 'RealPay debit order', 'payat': 'PayAt cash / retail',
             'eft': 'Direct EFT'}[channel],
            slot['count'],
            q2(slot['amount']),
            q2(slot['amount'] / (Decimal('1') + K.SA_VAT_RATE)),
        ])
    rows.append(['TOTAL — confirmed, bank-anchored',
                 s.net_collection_count,
                 s.confirmed_gwp_incl_vat,
                 ctx.cession.gwp_excl_vat])

    return Report(
        key='gwp', title='GWP Report', nmi='NMI 10.2.1',
        status=OK if s.confirmed_count else NIL_NO_ACTIVITY,
        columns=['Collection channel', 'Collections', f'GWP incl VAT ({K.CURRENCY})',
                 f'GWP excl VAT ({K.CURRENCY})'],
        rows=rows,
        kpis=[
            ('Confirmed collections (bank)', str(s.net_collection_count)),
            ('Confirmed GWP incl VAT', _m(s.confirmed_gwp_incl_vat)),
            ('GWP excl VAT', _m(ctx.cession.gwp_excl_vat)),
            ('Reversals deducted', f'{s.reversal_count} / {_m(s.reversals)}'),
        ],
        notes=[
            f'Bank-anchored: a policy counts only when the premium lands in FNB '
            f'{K.FNB_COLLECTION_ACCOUNT}. Graphite\'s payment status is NOT used here.',
            f'RealPay reversals are subtracted, not dropped — {s.reversal_count} '
            f'reversal(s) totalling {_m(s.reversals)} came out of the gross.',
            f'Graphite called {ctx.book.graphite_paid_count} policies successful; '
            f'the bank confirmed {s.net_collection_count}. Gap of '
            f'{ctx.book.bank_vs_graphite_gap} reported, not reconciled away.',
            f'VAT split at the SA rate of {K.SA_VAT_RATE:.0%} — this is the South '
            f'African book, not the Botswana one.',
        ],
        reconciled_against=(
            f'Every figure is the sum of classified lines on bank statement '
            f'{ctx.bank_statement_label or "(none)"}; the total ties to the Bank '
            f'Reconciliation report (NMI 10.2.2) line for line.'
        ),
    )


# ── 2. Bank Reconciliation (NMI 10.2.2) ─────────────────────────────────────
def build_bank_reconciliation(ctx: PackContext) -> Report:
    s = ctx.summary
    rows = [
        ['Premium credits included', s.confirmed_count, s.gross_credits],
        ['Less RealPay reversals', -s.reversal_count, -s.reversals],
        ['= Confirmed GWP incl VAT', s.net_collection_count, s.confirmed_gwp_incl_vat],
        ['Excluded — fees, interest, sweeps', s.excluded_count, s.excluded_total],
        ['Unclassified — needs a human', s.unclassified_count, s.unclassified_total],
    ]
    total_lines = (s.confirmed_count + s.reversal_count
                   + s.excluded_count + s.unclassified_count)
    rows.append(['Statement lines accounted for', total_lines, None])

    notes = [
        'Every line on the statement lands in exactly one bucket. If the line '
        'count here does not equal the statement\'s own line count, a line was '
        'lost and the pack must not be submitted.',
        'Exclusions are tested BEFORE inclusions, because a PayAt service-fee '
        'line contains the word PAYAT and would otherwise be counted as premium.',
    ]
    if s.unclassified_count:
        notes.append(
            f'🔴 {s.unclassified_count} line(s) totalling {_m(s.unclassified_total)} '
            f'matched no rule. They are in Open Items on the Master report and are '
            f'NOT in GWP. Do not submit until they are classified.'
        )

    return Report(
        key='bank-reconciliation', title='Bank Reconciliation', nmi='NMI 10.2.2',
        status=OK if total_lines else NIL_NO_ACTIVITY,
        columns=['Bucket', 'Lines', f'Amount ({K.CURRENCY})'],
        rows=rows,
        kpis=[
            ('Statement lines', str(total_lines)),
            ('Confirmed GWP incl VAT', _m(s.confirmed_gwp_incl_vat)),
            ('Unclassified lines', str(s.unclassified_count)),
        ],
        notes=notes,
        reconciled_against=(
            f'Bank statement {ctx.bank_statement_label or "(none)"} for '
            f'{ctx.period_label}, read through banking.BankStatementLine — the '
            f'same register the bank-feed import writes, with its database-level '
            f'duplicate guard.'
        ),
    )


# ── 3. Premium Variation ────────────────────────────────────────────────────
def build_premium_variation(ctx: PackContext) -> Report:
    if not ctx.has_prior_export:
        return Report(
            key='premium-variation', title='Premium Variation', status=BLOCKED,
            notes=['No prior-month Graphite export was supplied, so this month '
                   'cannot be compared to last month. Attach the prior-month '
                   'export and re-run. This is NOT a nil return.'],
            reconciled_against='',
        )

    cur = {p.policy_number: p for p in ctx.current_policies if p.active}
    pri = {p.policy_number: p for p in ctx.prior_policies if p.active}

    added = sorted(set(cur) - set(pri))
    removed = sorted(set(pri) - set(cur))
    changed = sorted(
        n for n in (set(cur) & set(pri))
        if cur[n].premium_incl_vat != pri[n].premium_incl_vat
    )

    rows = []
    for n in added:
        rows.append([n, 'Added', None, cur[n].premium_incl_vat, cur[n].premium_incl_vat])
    for n in removed:
        rows.append([n, 'Removed', pri[n].premium_incl_vat, None, -pri[n].premium_incl_vat])
    for n in changed:
        rows.append([n, 'Premium changed', pri[n].premium_incl_vat,
                     cur[n].premium_incl_vat,
                     q2(cur[n].premium_incl_vat - pri[n].premium_incl_vat)])

    movement = q2(sum((r[4] for r in rows if r[4] is not None), Decimal('0.00')))

    return Report(
        key='premium-variation', title='Premium Variation',
        status=OK if rows else NIL_NO_ACTIVITY,
        columns=['Policy number', 'Movement', f'Prior ({K.CURRENCY})',
                 f'Current ({K.CURRENCY})', f'Variation ({K.CURRENCY})'],
        rows=rows,
        kpis=[
            ('Policies added', str(len(added))),
            ('Policies removed', str(len(removed))),
            ('Premium changed', str(len(changed))),
            ('Net monthly premium movement', _m(movement)),
        ],
        notes=[
            'Policy numbers only — no client names leave the policy loader (DPA).',
            'This is a movement in the BOOK, not in collections. A policy can be '
            'on the book and not collect; the GWP report is the money.',
        ],
        reconciled_against=(
            'Current Graphite export vs the prior-month Graphite export, matched '
            'on policy number.'
        ),
    )


# ── 4. Cancellations ────────────────────────────────────────────────────────
def build_cancellations(ctx: PackContext) -> Report:
    """Who has run out of time to pay. See genric/config.py for the window."""
    book = ctx.book

    base_notes = [
        f'{book.total_active} active policies: {len(book.paid)} paid, '
        f'{len(book.failed)} failed, {len(book.dormant)} dormant.',
        'Recommend only — this report NEVER cancels a policy. Treaty Art. 10.4 '
        f'gives a {K.CANCELLATION_GRACE_MONTHS}-month grace and cover continues '
        'inside it.',
        'Every cancellation must follow the TCF fair-cancellation sequence: '
        'notify the failed collection in plain language with how to pay → give a '
        'fair pay window → send a final notice with the date → cancel and log the '
        'reason on the TCF dashboard.',
    ]

    if not is_unpaid_days_configured():
        return Report(
            key='cancellations', title='Cancellations', status=BLOCKED,
            kpis=[
                ('Active policies', str(book.total_active)),
                ('Failed collections', str(len(book.failed))),
                ('Dormant — no collection', str(len(book.dormant))),
                ('Potential candidates', str(len(book.cancellation_candidates))),
                ('Monthly premium at risk', _m(book.monthly_premium_at_risk)),
            ],
            notes=[
                f'🔴 BLOCKED — the pay window is blank. {UNPAID_DAYS_QUESTION} '
                f'Set "{UNPAID_DAYS_KEY}" in GENRIC Settings to that number of '
                f'days. The CFO set it to 30 on 14 Sep 2026, so a blank here '
                f'means somebody has since cleared it.',
                'Nothing was guessed. A guessed pay window either cancels a '
                'paying customer early or carries a non-payer past the treaty.',
                f'The counts above ARE real and ARE worth reading now: '
                f'{len(book.cancellation_candidates)} policies are potential '
                f'candidates and {_m(book.monthly_premium_at_risk)} a month is at '
                f'risk. What cannot be produced is the recommendation list — who '
                f'has actually run out of time.',
                'This is deliberately NOT a nil return. The old manual process '
                'reported this as nil while 71 policies were candidates.',
            ] + base_notes,
            reconciled_against='',
        )

    days = unpaid_days_before_cancellation()
    twice_failed = P.consecutive_failures(ctx.current_policies, ctx.prior_policies)
    as_at = ctx.period_end

    # ── The pay window, actually applied ───────────────────────────────────
    #
    # THE BOUNDARY, and which side of the line a policy at exactly `days` falls.
    #
    # The test is ``unpaid > days`` — STRICTLY greater. With the CFO's 30 days:
    #
    #     29 days unpaid  →  inside the window   →  NOT listed
    #     30 days unpaid  →  inside the window   →  NOT listed
    #     31 days unpaid  →  past the window     →  listed
    #
    # A policy at exactly 30 days falls on the SAFE side: it is still inside the
    # window and is not recommended for cancellation. The CFO's wording is
    # "policies unpaid for MORE THAN 30 days", and the customer was given 30
    # days to pay — on day 30 they still have that day. ``>=`` would cancel them
    # one day early, which is the more expensive mistake of the two: cancelling
    # a customer who still had time is a TCF complaint and lost cover, while
    # carrying a non-payer one extra day costs one day of premium. This is the
    # same convention as the 30-day statement terms the number was chosen to
    # match, where an invoice is overdue the day AFTER the terms run out.
    #
    # `as_at` is the period END, never the clock — see PolicyRow.unpaid_days.
    past_window, inside_window, unmeasurable = [], [], []
    for p in book.cancellation_candidates:
        unpaid = p.unpaid_days(as_at)
        if unpaid is None:
            unmeasurable.append(p)
        elif unpaid > days:
            past_window.append((p, unpaid))
        else:
            inside_window.append((p, unpaid))

    rows = []
    for p, unpaid in sorted(past_window, key=lambda x: (-x[1], x[0].policy_number)):
        age = p.age_months(as_at)
        rows.append([
            p.policy_number,
            P.STATUS_LABELS[p.status],
            p.last_failed_collection_at.isoformat(),
            unpaid,
            p.created_at.isoformat() if p.created_at else '(no inception date)',
            age if age is not None else '',
            'Yes' if p.policy_number in twice_failed else 'No',
            'Yes' if (age is not None and age > K.POLICY_AGE_FLAG_MONTHS) else 'No',
            p.premium_incl_vat,
        ])

    premium_past_window = q2(sum((p.premium_incl_vat for p, _ in past_window),
                                 Decimal('0.00')))

    notes = [
        f'PAY WINDOW APPLIED: {days} days. Every policy listed here has been '
        f'unpaid for MORE THAN {days} days as at {as_at.isoformat()}, counted '
        f'from the date of its failed or missing collection. The window is the '
        f'"{UNPAID_DAYS_KEY}" setting in Omni Admin → GENRIC Settings, changed '
        f'on screen with no deploy — change it and the next pack re-draws this '
        f'list against the new number.',
        f'A policy unpaid for exactly {days} days is NOT listed — it is still '
        f'inside the window and has that day to pay. The first day a policy '
        f'appears here is day {days + 1}.',
        f'{len(inside_window)} further candidate(s) are unpaid but still inside '
        f'the {days}-day window, so they are counted at risk and NOT '
        f'recommended for cancellation.',
        f'"2 consecutive months" is confirmed against the prior-month export; '
        f'{len(twice_failed)} policy(ies) failed in both months.',
        f'Policies over {K.POLICY_AGE_FLAG_MONTHS} months old are flagged '
        f'(treaty Appendix 1/3) — this needs Graphite createdAt, and policies '
        f'with no inception date are shown as such rather than assumed young.',
    ] + base_notes

    if unmeasurable:
        # The honest half. These are NOT counted as past the window and NOT
        # counted as inside it — the export simply did not say. Silently putting
        # them in either bucket is what this whole report exists not to do.
        notes.insert(1, (
            f'🔴 {len(unmeasurable)} of {len(book.cancellation_candidates)} '
            f'candidate(s) carry NO failed-collection date in the export, so the '
            f'{days}-day window could not be measured for them. They are NOT on '
            f'the list below and they are NOT cleared either — they are unknown. '
            f'The export needs a failed-collection date column on every row '
            f'before this list can be called complete.'
        ))

    if not ctx.has_prior_export:
        notes.insert(1, '🔴 No prior-month export supplied, so "2 consecutive '
                        'months" could not be confirmed for any policy. Every '
                        'row shows No. Do not act on this list until the prior '
                        'month is attached.')

    return Report(
        key='cancellations',
        title=f'Cancellations — policies unpaid for more than {days} days',
        status=OK if rows else NIL_NO_ACTIVITY,
        columns=['Policy number', 'Payment status', 'Failed collection',
                 'Days unpaid', 'Inception', 'Age (months)',
                 '2 consecutive months failed', f'Over {K.POLICY_AGE_FLAG_MONTHS} months',
                 f'Monthly premium ({K.CURRENCY})'],
        rows=rows,
        kpis=[
            ('Pay window', f'More than {days} days unpaid'),
            ('Active policies', str(book.total_active)),
            (f'Unpaid more than {days} days — recommend cancellation',
             str(len(rows))),
            (f'Unpaid but still inside the {days} days', str(len(inside_window))),
            ('No failed-collection date — cannot be measured',
             str(len(unmeasurable))),
            ('Failed collections', str(len(book.failed))),
            ('Dormant — no collection', str(len(book.dormant))),
            (f'Monthly premium past the {days} days', _m(premium_past_window)),
            ('Monthly premium at risk (all candidates)',
             _m(book.monthly_premium_at_risk)),
        ],
        notes=notes,
        reconciled_against=(
            f'Graphite policy book, classified against the bank. Candidate counts '
            f'tie to the Client Listing report. The {days}-day pay window is '
            f'measured from each policy\'s failed-collection date to '
            f'{as_at.isoformat()}, the period end — not to today.'
        ),
    )


# ── 5. New Business ─────────────────────────────────────────────────────────
def build_new_business(ctx: PackContext) -> Report:
    start = date(ctx.year, ctx.month, 1)
    end = ctx.period_end

    dated = [p for p in ctx.current_policies if p.created_at]
    undated = [p for p in ctx.current_policies if not p.created_at]
    new = [p for p in dated if start <= p.created_at <= end]

    rows = [[p.policy_number, p.created_at.isoformat(),
             P.STATUS_LABELS[p.status], p.premium_incl_vat]
            for p in sorted(new, key=lambda x: (x.created_at, x.policy_number))]

    notes = [
        'New business is policies whose Graphite createdAt / inception date '
        'falls in the reporting month.',
    ]
    if undated:
        notes.append(
            f'🔴 {len(undated)} policy(ies) have no inception date in the export, '
            f'so they can be neither counted as new business nor aged for the '
            f'{K.POLICY_AGE_FLAG_MONTHS}-month rule. They are in Open Items. The '
            f'export needs createdAt on every row.'
        )

    return Report(
        key='new-business', title='New Business',
        status=OK if rows else NIL_NO_ACTIVITY,
        columns=['Policy number', 'Inception', 'Payment status',
                 f'Monthly premium ({K.CURRENCY})'],
        rows=rows,
        kpis=[
            ('New policies this month', str(len(rows))),
            ('Monthly premium written', _m(sum((p.premium_incl_vat for p in new),
                                               Decimal('0.00')))),
            ('Policies with no inception date', str(len(undated))),
        ],
        notes=notes,
        reconciled_against=(
            'Graphite all-policy export, filtered on createdAt within the month. '
            'Not the bank — a policy can incept and not yet collect.'
        ),
    )


# ── 6. Client Listing ───────────────────────────────────────────────────────
def build_client_listing(ctx: PackContext) -> Report:
    rows = [[p.policy_number,
             P.STATUS_LABELS[p.status],
             p.created_at.isoformat() if p.created_at else '',
             p.premium_incl_vat,
             'Active' if p.active else 'Inactive']
            for p in sorted(ctx.current_policies, key=lambda x: x.policy_number)]

    return Report(
        key='client-listing', title='Client Listing',
        status=OK if rows else NIL_NO_ACTIVITY,
        columns=['Policy number', 'Payment status', 'Inception',
                 f'Monthly premium ({K.CURRENCY})', 'Book status'],
        rows=rows,
        kpis=[
            ('Policies on the book', str(len(rows))),
            ('Active', str(ctx.book.total_active)),
        ],
        notes=[
            'POLICY NUMBERS ONLY — this is a client listing without client '
            'identities, by design (DPA). Names, ID numbers, addresses, contact '
            'details and bank details are dropped when the export is loaded, not '
            'filtered at render, so there is no path by which one reaches this '
            'file, an email or a log.',
        ],
        reconciled_against='Graphite all-policy export; row count ties to the '
                           'Master report\'s Policy Source sheet.',
    )


# ── 7-11. Claims-side reports ───────────────────────────────────────────────
_NO_CLAIMS_SOURCE = (
    'No claims source is wired for the {entity} {book} book. Omni\'s '
    'integrations.GraphiteClaim carries the BOTSWANA book — using it here would '
    'put the wrong country\'s claims in a South African regulatory return. This '
    'report is therefore NOT a nil return: it is unproduced, and needs a claims '
    'feed for this book before it can be filed.'
).format(entity=K.ENTITY_NAME, book=K.ENTITY_BOOK)


def _claims_stub(key: str, title: str, columns: list, extra_notes: list = None) -> Report:
    return Report(
        key=key, title=title, status=NIL_NO_SOURCE,
        columns=columns, rows=[],
        kpis=[('Status', 'No source — not a nil return')],
        notes=[_NO_CLAIMS_SOURCE] + (extra_notes or []),
        reconciled_against='',
    )


def build_large_loss(ctx: PackContext) -> Report:
    return _claims_stub(
        'large-loss', 'Large Loss',
        ['Claim number', 'Policy number', 'Date of loss', 'Reported',
         f'Incurred ({K.CURRENCY})', 'Status'],
        ['A second thing is missing even once a feed exists: the large-loss '
         'threshold. It is not in the treaty extract or the build prompt, and it '
         'is not guessed here.'],
    )


def build_legal(ctx: PackContext) -> Report:
    return _claims_stub(
        'legal', 'Legal',
        ['Matter reference', 'Policy number', 'Opened', 'Stage',
         f'Reserve ({K.CURRENCY})'],
        ['Omni\'s BONU legal module covers Alpha Direct Botswana, not this book.'],
    )


def build_complaints(ctx: PackContext) -> Report:
    return _claims_stub(
        'complaints', 'Complaints',
        ['Complaint reference', 'Policy number', 'Received', 'Category',
         'Status', 'Days open'],
        ['There is no complaints register for this book in Omni today. A nil '
         'complaints return that was never checked is a regulatory finding.'],
    )


def build_claims_incurred(ctx: PackContext) -> Report:
    return _claims_stub(
        'claims-incurred', 'Claims Incurred',
        ['Month reported', 'Claims', f'Paid ({K.CURRENCY})',
         f'Outstanding reserve ({K.CURRENCY})', f'Incurred ({K.CURRENCY})'],
    )


def build_claims_triangulation(ctx: PackContext) -> Report:
    return _claims_stub(
        'claims-triangulation', 'Claims Triangulation',
        ['Accident month', 'Dev 0', 'Dev 1', 'Dev 2', 'Dev 3', 'Dev 4+'],
        ['A triangulation also needs history the nightly Graphite sync cannot '
         'give: GraphiteClaim is overwritten with today\'s position every night, '
         'so what a reserve WAS at each development point is not recoverable. '
         'This report needs a stored monthly snapshot, which does not exist yet.'],
    )


# ── 12. IFRS 17 ─────────────────────────────────────────────────────────────
def build_ifrs17(ctx: PackContext) -> Report:
    """Revenue/CSM disclosure. Reads only — it may not post or remap anything."""
    return Report(
        key='ifrs-17', title='IFRS 17', status=BLOCKED,
        columns=['Measure', f'Amount ({K.CURRENCY})', 'Basis'],
        rows=[],
        kpis=[
            ('Confirmed GWP incl VAT', _m(ctx.summary.confirmed_gwp_incl_vat)),
            ('Ceded to GENRIC', _m(ctx.cession.ceded)),
            ('Retained', _m(ctx.cession.retained)),
        ],
        notes=[
            '🔴 NOT produced. An IFRS 17 disclosure is an earned-premium and CSM '
            'measurement, not a collections roll-up: it needs the unearned '
            'premium reserve brought forward, the risk adjustment, and the '
            'coverage-unit pattern for this book. None of those are inputs to '
            'this pack.',
            'The three figures above are the pack\'s own, and are correct as '
            'WRITTEN premium and cession. They are NOT an IFRS 17 result and must '
            'not be filed as one.',
            'Building this properly touches the revenue format and the '
            'management-accounts P&L, which are FROZEN since 13 May 2026. It '
            'needs the CFO first.',
        ],
        reconciled_against='',
    )


# ── 13. Master ──────────────────────────────────────────────────────────────
def build_master(ctx: PackContext, others: Sequence[Report]) -> Report:
    """Control, All Report Figures, Bank Source, Policy Source, Open Items.

    Built LAST, from the other twelve — so the Master can never quote a figure a
    report does not contain.
    """
    s, ces, book = ctx.summary, ctx.cession, ctx.book

    rows = [['Report', 'Status', 'Rows', 'Headline']]
    for r in others:
        headline = r.kpis[0][1] if r.kpis else ''
        rows.append([r.title + (f' ({r.nmi})' if r.nmi else ''),
                     STATUS_LABELS[r.status], len(r.rows), headline])

    open_items = []
    if s.unclassified_count:
        open_items.append(
            f'{s.unclassified_count} bank line(s) totalling '
            f'{_m(s.unclassified_total)} matched no classification rule.')
    if not is_unpaid_days_configured():
        open_items.append(f'🔴 CFO: {UNPAID_DAYS_QUESTION}')
    # The pay window's sibling question, and the only one still unanswered.
    # It was never listed here, because the invoice is not one of the reports
    # this loop walks — so the pack's own control sheet said "OPEN ITEMS" while
    # silently omitting the one thing that stops the invoice being paid. Now
    # that the pay window is answered it would otherwise read as all clear.
    if not is_remit_to_configured():
        open_items.append(
            f'🔴 CFO: {REMIT_TO_QUESTION} Until then the invoice prints '
            f'"NOT ON FILE, DO NOT PAY AGAINST THIS DOCUMENT" and names no '
            f'account — nothing is guessed.')
    # The pay window is set, but it can only be measured where the export
    # carries a failed-collection date. Say how many it could not measure.
    if is_unpaid_days_configured():
        unmeasured = len([p for p in book.cancellation_candidates
                          if p.unpaid_days(ctx.period_end) is None])
        if unmeasured:
            open_items.append(
                f'{unmeasured} cancellation candidate(s) have no '
                f'failed-collection date, so the pay window could not be '
                f'measured for them — they are neither recommended nor cleared.')
    if ctx.invoice_needs_confirmation:
        open_items.append(
            f'Invoice number {ctx.invoice_number} was derived from the last pack, '
            f'not confirmed. Confirm it before the invoice leaves.')
    if not ctx.has_prior_export:
        open_items.append(
            'No prior-month Graphite export — premium variation is blocked and '
            '"2 consecutive months failed" cannot be confirmed.')
    undated = len([p for p in ctx.current_policies if not p.created_at])
    if undated:
        open_items.append(
            f'{undated} policy(ies) have no inception date, so new business and '
            f'the {K.POLICY_AGE_FLAG_MONTHS}-month flag are incomplete.')
    if book.unknown:
        open_items.append(
            f'{len(book.unknown)} policy(ies) carry a payment status this pack '
            f'does not recognise.')
    no_source = [r.title for r in others if r.status == NIL_NO_SOURCE]
    if no_source:
        open_items.append(
            'Unproduced for want of a source (NOT nil returns): '
            + ', '.join(no_source) + '.')
    blocked = [r.title for r in others if r.status == BLOCKED]
    if blocked:
        open_items.append('Blocked: ' + ', '.join(blocked) + '.')

    return Report(
        key='master', title='Master — Control',
        status=OK,
        columns=['Report', 'Status', 'Rows', 'Headline'],
        rows=rows[1:],
        kpis=[
            ('Period', ctx.period_label),
            ('Entity', f'{K.ENTITY_NAME} ({K.ENTITY_BOOK})'),
            ('Confirmed GWP incl VAT', _m(s.confirmed_gwp_incl_vat)),
            ('GWP excl VAT', _m(ces.gwp_excl_vat)),
            (f'Ceded at {K.QUOTA_SHARE_CEDED:.0%}', _m(ces.ceded)),
            (f'Ceding commission {K.CEDING_COMMISSION_RATE:.1%}', _m(ces.ceding_commission)),
            ('NET REINSURANCE PREMIUM DUE', _m(ces.net_reinsurance_premium_due)),
            ('Invoice number', ctx.invoice_number or '(unconfirmed)'),
            ('Invoice VAT', _m(Decimal('0.00')) + ' — cross-border, zero-rated'),
            ('Active policies', str(book.total_active)),
            ('Cancellation candidates', str(len(book.cancellation_candidates))),
            ('Monthly premium at risk', _m(book.monthly_premium_at_risk)),
        ],
        notes=[
            f'Bank source: {ctx.bank_statement_label or "(none)"} — FNB '
            f'{K.FNB_COLLECTION_ACCOUNT}, read through banking.BankStatementLine.',
            f'Policy source: {ctx.policy_source or "(none)"}.',
            'OPEN ITEMS: ' + ('; '.join(open_items) if open_items else 'none.'),
            'No journal was posted and no GL mapping was changed. This pack reads '
            'and reports.',
        ],
        reconciled_against=(
            'Every figure on this sheet is copied from the report named beside '
            'it — the Master computes nothing of its own.'
        ),
    )


STATUS_LABELS = {
    OK: 'Produced',
    NIL_NO_ACTIVITY: 'Nil return — source read, nothing to report',
    NIL_NO_SOURCE: '🔴 NOT produced — no source system',
    BLOCKED: '🔴 BLOCKED — missing input or decision',
}


#: Build order. Master is appended last by build_all().
BUILDERS = (
    build_gwp,
    build_bank_reconciliation,
    build_premium_variation,
    build_cancellations,
    build_new_business,
    build_client_listing,
    build_large_loss,
    build_legal,
    build_complaints,
    build_claims_incurred,
    build_claims_triangulation,
    build_ifrs17,
)


def build_all(ctx: PackContext) -> list:
    """The thirteen: twelve reports plus the Master built from them.

    Note for Finance: the build prompt's heading says "13 reports + master" but
    the list beneath it names the Master and then twelve reports. This build
    produces those thirteen sheets. Flagged rather than silently resolved.
    """
    others = [b(ctx) for b in BUILDERS]
    return others + [build_master(ctx, others)]
