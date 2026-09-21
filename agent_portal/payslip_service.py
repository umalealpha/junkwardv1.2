"""
agent_portal/payslip_service.py — build UniCoin Instant Insurance commission
payslips for a cycle.

Each agent's PAYABLE commission for the cycle (which ties to the uploaded
pay-run) is itemised by stream, then Bharath's tax method (agent_portal.tax) is
applied to the agent's monthly total to get the net payable. One AgentPayslip
per (cycle, agent); regenerating overwrites in place.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from .models import AgentPayslip, CommissionLine, CommissionCycle
from .tax import agent_tax

ZERO = Decimal('0.00')

# Engine stream key -> the label an agent should see on the payslip. Several
# engine keys fold into one agent-facing line (e.g. MIS/Liberty both = New Sales).
STREAM_LABELS = {
    'new_sales_mis': 'New Sales',
    'new_sales_liberty': 'New Sales',
    'conversion': 'RealPay Conversion',
    'conversion_mis': 'RealPay Conversion',
    'conversion_liberty': 'RealPay Conversion',
    'collection': 'Collection',
    'bank_confirmation': 'Collection',
    'motor': 'Motor Comprehensive',
    'kyc_claims': 'KYC & Banking',
    'kyc': 'KYC & Banking',
    'backlog': 'Backlog',
    'reprocessed': 'Reprocessed',
    'hospital': 'Hospital Cashback',
    'incentives': 'Incentives',
}
# Order the agent-facing lines print in.
_LABEL_ORDER = ['New Sales', 'RealPay Conversion', 'Collection', 'Motor Comprehensive',
                'KYC & Banking', 'Backlog', 'Hospital Cashback', 'Incentives', 'Reprocessed']


def _label(stream_key: str) -> str:
    return STREAM_LABELS.get(stream_key, stream_key.replace('_', ' ').title())


def _cycle_prefix(cycle: CommissionCycle) -> str:
    """UNI-YYMM from the cycle end date (payslip-number stem)."""
    d = cycle.end_date
    return f"UNI-{d:%y%m}" if d else f"UNI-{cycle.id.hex[:4].upper()}"


def agent_breakdown(cycle: CommissionCycle, agent):
    """Return (earnings, gross, not_paid) for one agent:
      earnings = [(label, amount)] of PAYABLE commission, by stream label, ordered
      gross    = Σ earnings (ties to the pay-run)
      not_paid = up to 12 [label, policy_ref, reason] rejected items (transparency)
    """
    sums: dict[str, Decimal] = defaultdict(lambda: ZERO)
    not_paid: list[list[str]] = []
    for l in CommissionLine.objects.filter(cycle=cycle, agent=agent):
        if l.payable:
            sums[_label(l.stream)] += (l.commission or ZERO)
        elif len(not_paid) < 12 and (l.reason or l.policy_ref):
            not_paid.append([_label(l.stream), l.policy_ref or '', (l.reason or '')[:120]])
    items = [(lbl, amt) for lbl, amt in sums.items() if amt]
    items.sort(key=lambda x: (_LABEL_ORDER.index(x[0]) if x[0] in _LABEL_ORDER else 99, x[0]))
    gross = sum((a for _, a in items), ZERO)
    return items, gross, not_paid


@transaction.atomic
def build_payslips_for_cycle(cycle: CommissionCycle, user=None) -> dict:
    """Create/update an AgentPayslip for every agent with payable commission in
    the cycle. Idempotent. Returns a tally + the run total for reconciliation.

    Refuses once the cycle is signed off: rebuilding after approval/paid could
    change a slip an agent has already been emailed (DeepSeek review 2026-07-27).
    Reopen the cycle first if a correction is genuinely needed."""
    if cycle.status in (CommissionCycle.Status.APPROVED, CommissionCycle.Status.PAID):
        raise ValueError('Pay cycle is signed off — reopen it before rebuilding payslips.')
    # Agents with any payable line this cycle.
    agent_ids = (CommissionLine.objects.filter(cycle=cycle, payable=True)
                 .values_list('agent_id', flat=True).distinct())
    from .models import Agent
    agents = {a.id: a for a in Agent.objects.filter(id__in=list(agent_ids))}

    # Stable payslip numbers: keep an existing number, else assign the next in
    # sequence ordered by agent name so a re-run doesn't renumber everyone.
    existing = {p.agent_id: p for p in AgentPayslip.objects.filter(cycle=cycle)}
    prefix = _cycle_prefix(cycle)
    used = {p.number for p in existing.values()}
    seq = 0

    def next_number():
        nonlocal seq
        while True:
            seq += 1
            n = f"{prefix}-{seq:04d}"
            if n not in used:
                used.add(n)
                return n

    created = updated = 0
    gross_total = net_total = tax_total = ZERO
    built_ids: list = []
    ordered_agents = sorted(agents.values(), key=lambda a: a.name)
    for agent in ordered_agents:
        items, gross, not_paid = agent_breakdown(cycle, agent)
        if gross <= 0:
            continue
        t = agent_tax(gross)
        ps = existing.get(agent.id)
        number = ps.number if ps else next_number()
        obj, was_created = AgentPayslip.objects.update_or_create(
            cycle=cycle, agent=agent,
            defaults={
                'number': number,
                'gross': t.gross, 'ex_vat': t.ex_vat, 'annual': t.annual,
                'tax': t.tax, 'net': t.net,
                'breakdown': [[lbl, str(amt)] for lbl, amt in items],
                'not_paid': not_paid,
                'generated_by': user,
            },
        )
        created += int(was_created)
        updated += int(not was_created)
        built_ids.append(agent.id)
        gross_total += t.gross
        tax_total += t.tax
        net_total += t.net

    # Drop stale slips: an agent removed from a corrected re-import must not keep
    # an old payslip a manager could still email (Fable review 2026-07-27).
    removed = (AgentPayslip.objects.filter(cycle=cycle)
               .exclude(agent_id__in=built_ids).delete()[0])

    return {
        'cycle': cycle.label,
        'payslips': created + updated,
        'created': created, 'updated': updated, 'removed': removed,
        'gross_total_bwp': str(gross_total),
        'tax_total_bwp': str(tax_total),
        'net_total_bwp': str(net_total),
    }
