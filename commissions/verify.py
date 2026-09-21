"""
commissions/verify.py — the "Commission Verify" half of Build Spec B13.

It VERIFIES. It does not rebuild, and it does not widen scope.

The source prompt is explicit about both halves:

    "Confirm commissions/payroll_feed.py population = Finance's intent (which
     groups feed, which agents are excluded). If a gap is found, raise it; do
     not silently widen scope."

Finance's stated intent lives in ONE config key, ``commission.payroll_group_filter``,
whose initial value is ``pays_via == PAYROLL`` — exactly the filter
``commissions.payroll_feed.feed_period`` applies. Independent / direct-bank
agents keep the payout file and the bank run; they are excluded ON PURPOSE and
are never fed here.

What this reports, per month:

* the intent, read from config, and whether the code still implements it — a
  config key that says something the code does not do is itself a gap, and it
  is reported rather than quietly honoured one way or the other;
* the payroll-group population: how many active agents, how many can actually
  be resolved to a staff Employee by e-mail (the feed matches on e-mail only,
  never on a name);
* approved payroll-group submissions for the month that are NOT fed, one line
  per agent with the reason;
* scope drift in the other direction — a DIRECT-BANK submission that somehow
  carries a payroll amendment. That one is loud: it means somebody was paid
  through payroll who was meant to be paid by the bank run.

Nothing here writes. It returns a dict; the orchestrator prints it, e-mails it
and marks the run ATTENTION when ``gaps`` is non-empty.
"""
from __future__ import annotations

from typing import Any

# The one place the stated intent is named. Anything else is a gap to report.
INTENT_KEY = 'commission.payroll_group_filter'
INTENT_IMPLEMENTED = 'pays_via == PAYROLL'


def stated_intent() -> str:
    """Finance's stated commission-feed population, from config."""
    from payroll import config as payroll_config
    return (payroll_config.get_setting(INTENT_KEY, INTENT_IMPLEMENTED) or '').strip()


def verify_population(*, period_label: str) -> dict[str, Any]:
    """Compare what the commission feed WOULD cover against the stated intent.

    Read-only. Returns:
        {'intent', 'implemented', 'groups', 'agents', 'agents_resolvable',
         'fed', 'unfed', 'unfed_detail', 'gaps'}
    """
    from django.db.models import Q

    from commissions.models import (CommissionAgent, CommissionGroup,
                                    CommissionSubmission)
    from commissions.payroll_feed import _employee_for_agent

    intent = stated_intent()
    gaps: list[str] = []

    if intent != INTENT_IMPLEMENTED:
        gaps.append(
            f'Config {INTENT_KEY} says "{intent}" but the commission feed '
            f'implements "{INTENT_IMPLEMENTED}". The code was NOT widened to '
            f'match the config — someone has to decide which is right.')

    payroll_groups = list(CommissionGroup.objects
                          .filter(pays_via=CommissionGroup.PaysVia.PAYROLL)
                          .order_by('name'))
    for g in payroll_groups:
        if not g.is_active:
            gaps.append(
                f'Group "{g.name}" is set to pay through payroll but is marked '
                f'inactive — its agents feed nothing. Deliberate, or an oversight?')

    agents = list(CommissionAgent.objects.filter(group__in=payroll_groups))
    resolvable, unresolvable = [], []
    for a in agents:
        (resolvable if _employee_for_agent(a) is not None else unresolvable).append(a)
    for a in unresolvable:
        gaps.append(
            f'Agent "{a.name}" is in a payroll group but cannot be matched to a '
            f'staff record by e-mail, so nothing of theirs can ever reach a '
            f'payslip. The feed refuses to guess by name.')

    subs = (CommissionSubmission.objects
            .select_related('agent', 'group')
            .filter(period_label=period_label,
                    group__pays_via=CommissionGroup.PaysVia.PAYROLL))
    fed = subs.filter(payroll_amendment__isnull=False).count()
    unfed_qs = subs.filter(payroll_amendment__isnull=True,
                           status=CommissionSubmission.Status.APPROVED)
    unfed_detail = []
    for s in unfed_qs:
        reason = ('no matching staff employee (e-mail)'
                  if _employee_for_agent(s.agent) is None
                  else 'nil or negative net payable'
                  if (s.net_payable or 0) <= 0
                  else 'approved but not fed — run the commission feed')
        unfed_detail.append({'agent': s.agent.name, 'reason': reason})

    # Scope drift the other way: bank-paid commission that reached payroll.
    strays = (CommissionSubmission.objects
              .select_related('agent')
              .filter(~Q(group__pays_via=CommissionGroup.PaysVia.PAYROLL),
                      period_label=period_label,
                      payroll_amendment__isnull=False))
    for s in strays:
        gaps.append(
            f'SCOPE DRIFT: "{s.agent.name}" is in a direct-bank group but their '
            f'{period_label} commission carries a payroll amendment. They may '
            f'be set to be paid twice — check before the close.')

    if unfed_detail:
        gaps.append(f'{len(unfed_detail)} approved payroll-group commission '
                    f'submission(s) for {period_label} are not on a payroll '
                    f'batch. Listed below; scope was NOT widened to sweep them in.')

    return {
        'intent': intent,
        'implemented': INTENT_IMPLEMENTED,
        'groups': [g.name for g in payroll_groups],
        'agents': len(agents),
        'agents_resolvable': len(resolvable),
        'fed': fed,
        'unfed': len(unfed_detail),
        'unfed_detail': unfed_detail,
        'gaps': gaps,
    }
