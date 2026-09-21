"""
bonu/retainer.py — did we get what we paid the retainer for?

CFO 2026-08-03: Jeremiah and Taldi are paid **P85,000 a month** to manage around
**40 cases**, "but sometimes they are not managing 40 cases and things are slipping out".

The invoice cannot answer this. It says P85,000 every month whatever happens. So the
scorecard is built from the CASE REGISTER instead, and it answers four questions:

  1. **Did they carry the caseload?**  cases under management vs the committed number.
  2. **What did each case actually cost us?**  fee / cases carried. At 40 cases a case
     costs P2,125. At 24 cases the same money buys P3,542 a case — a 67% overpay that
     no invoice would ever show.
  3. **Is anything slipping?**  open cases with no recorded movement beyond the SLA,
     cases never started, court dates passed with no update, cases that just went quiet.
  4. **What is the retainer NOT earning?**  the shortfall in cases x the intended fee
     per case — the number to take into the next fee conversation.

Pure functions over rows. No writes, no GL.
"""
from __future__ import annotations

from decimal import Decimal
from django.utils import timezone


def _d(x):
    return Decimal(str(x or 0))


def scorecard(retainer, cases, as_of=None):
    """One retainer, one month-end view. `cases` = that retainer\'s LegalCase rows."""
    as_of = as_of or timezone.localdate()
    cases = list(cases)
    open_cases = [c for c in cases if c.is_open]
    closed = [c for c in cases if not c.is_open]

    committed = retainer.committed_cases or 0
    carried = len(open_cases)
    per_committed = retainer.fee_per_committed_case
    actual_per_case = (_d(retainer.monthly_fee) / carried).quantize(Decimal('0.01')) if carried else None

    # Slippage, each with the cases named so it can be acted on.
    stale = [c for c in open_cases
             if (c.days_quiet(as_of) or 0) > retainer.max_days_no_activity]
    never_started = [c for c in open_cases
                     if c.first_action_on is None
                     and (as_of - c.instructed_on).days > retainer.max_days_to_first_action]
    overdue_action = [c for c in open_cases
                      if c.next_action_due and c.next_action_due < as_of]
    court_passed = [c for c in open_cases
                    if c.court_date and c.court_date < as_of
                    and (c.last_activity_on is None or c.last_activity_on < c.court_date)]
    abandoned = [c for c in cases if c.status == 'abandoned']

    shortfall_cases = max(0, committed - carried)
    # With no case register the shortfall is unknown, not equal to the whole fee. Reporting
    # P85,000 unearned because we have not loaded a case list is a false figure, and a false
    # figure in a letter to a law firm is worse than no letter.
    if not cases:
        shortfall_cases = None
        unearned = None
    else:
        unearned = ((_d(per_committed) * shortfall_cases).quantize(Decimal('0.01'))
                    if per_committed else None)

    return {
        'retainer': retainer.name,
        'firm': retainer.firm.name,
        'monthly_fee': float(retainer.monthly_fee),
        'committed_cases': committed,
        'cases_carried': carried,
        'cases_closed_ever': len(closed),
        'utilisation_pct': round(100 * carried / committed, 1) if committed else None,
        'fee_per_committed_case': float(per_committed) if per_committed else None,
        'actual_cost_per_case': float(actual_per_case) if actual_per_case else None,
        'overpay_per_case': (float(actual_per_case - per_committed)
                             if (actual_per_case and per_committed) else None),
        'shortfall_cases': shortfall_cases,      # None = we cannot tell yet
        'retainer_not_earned': float(unearned) if unearned is not None else None,
        'slippage': {
            'stale': [{'case_ref': c.case_ref, 'days_quiet': c.days_quiet(as_of),
                       'matter_type': c.matter_type} for c in stale],
            'never_started': [{'case_ref': c.case_ref,
                                'days_since_instructed': (as_of - c.instructed_on).days}
                               for c in never_started],
            'action_overdue': [{'case_ref': c.case_ref,
                                 'due': c.next_action_due.isoformat(),
                                 'days_late': (as_of - c.next_action_due).days}
                                for c in overdue_action],
            'court_date_passed_no_update': [{'case_ref': c.case_ref,
                                             'court_date': c.court_date.isoformat()}
                                            for c in court_passed],
            'abandoned': [{'case_ref': c.case_ref} for c in abandoned],
        },
        'slippage_total': (len(stale) + len(never_started) + len(overdue_action)
                           + len(court_passed) + len(abandoned)),
        'sla': {'max_days_no_activity': retainer.max_days_no_activity,
                 'max_days_to_first_action': retainer.max_days_to_first_action},
        'verdict': _verdict(committed, carried, len(stale) + len(never_started),
                            has_register=bool(cases)),
        'has_case_register': bool(cases),
    }


def _verdict(committed, carried, slipping, has_register=True):
    """One plain line for the CFO, because a table of numbers is not a decision."""
    if not committed:
        return 'No committed caseload recorded — the retainer cannot be measured yet.'
    if not has_register:
        # An empty case register is OUR gap, not evidence against the firm. Saying "0 of 40
        # cases, we are overpaying" would be an accusation built on missing data — and the
        # firm's invoices show they are plainly doing work.
        return (f'No case register yet, so we cannot tell what the {committed}-case commitment '
                f'actually bought. Ask the firm for its current case list — that single list '
                f'turns this page on.')
    pct = 100 * carried / committed
    if pct >= 95 and slipping == 0:
        return 'Carrying the full caseload with nothing slipping — the retainer is earning its fee.'
    if pct >= 95:
        return f'Caseload is there, but {slipping} case(s) are not moving. Ask for a status report.'
    if pct >= 75:
        return (f'Carrying {carried} of {committed} cases. Under-utilised — the same money is '
                f'buying fewer cases than agreed.')
    return (f'Only {carried} of {committed} cases under management. On this basis we are '
            f'materially overpaying per case — put the fee or the caseload back on the table.')


def double_dipping(retainer, cases, invoice_lines):
    """Retainer firms billing hourly for work the flat fee already covers.

    The clearest way a retainer leaks: P85,000 lands every month AND invoices arrive
    for the same matters. Matches invoice lines to retainer case refs.
    """
    refs = {(c.case_ref or '').strip().lower() for c in cases if c.case_ref}
    hits = []
    for l in invoice_lines:
        if l.invoice.firm_id != retainer.firm_id:
            continue
        m = (l.matter_ref or '').strip().lower()
        if m and m in refs:
            hits.append({'case_ref': l.matter_ref, 'invoice': l.invoice.invoice_number,
                         'amount': float(l.amount or 0),
                         'service_date': l.service_date.isoformat() if l.service_date else None})
    return {'count': len(hits), 'amount': round(sum(h['amount'] for h in hits), 2),
            'lines': hits[:100]}
