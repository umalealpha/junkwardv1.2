"""commissions/broker_summary.py — the per-broker commission summary (C7).

READ-ONLY AND MARKED PREVIEW. Nothing here writes, posts or pays. The maths is
not signed off: Finance has not yet sent the August workbook rows that would
prove it to the cent, so the endpoint returns ``signed_off: False`` and the
screen says so at the top.

Two refusals are deliberate, and each one exists because the alternative is a
confident wrong number on a money screen:

  1. No rate row configured for the period -> no commission figures at all.
     Rates nobody set are invented rates.
  2. The RealPay outcome feed not reporting for the window -> the whole page is
     flagged incomplete. Debits raised whose result never came back make
     'collected' look like zero, and zero must never render the same as unknown.

The motor / non-motor split is NOT a refusal case: it is decided per policy from
whether the policy has a vehicle in Graphite, upstream in graphite_feed, so a
broker always has a split and never blocks on it.
"""
from __future__ import annotations

import datetime as _dt
import logging

from commissions import brokers as svc
from commissions.commission_calc import Rates, compute, growth_pct, money
from commissions.models import (Broker, BrokerCommissionRate, BrokerCompliance,
                                BrokerMonthClose)
from realpay import graphite_feed

log = logging.getLogger(__name__)


def month_bounds(period: str):
    """'YYYY-MM' -> (first day, first day of NEXT month).

    The end is EXCLUSIVE on purpose: the feed windows on created_at, a datetime,
    so an inclusive last-day boundary silently drops everything collected after
    midnight on the last day of the month.
    """
    year, mon = int(period[:4]), int(period[5:7])
    start = _dt.date(year, mon, 1)
    end = _dt.date(year + (mon == 12), (mon % 12) + 1, 1)
    return start, end


def previous_period(period: str) -> str:
    year, mon = int(period[:4]), int(period[5:7])
    return f'{year - (mon == 1)}-{(mon - 2) % 12 + 1:02d}'


def _alias_map():
    """{graphite agency name: Broker} for every alias, plus the broker list."""
    out = {}
    brokers = list(Broker.objects.filter(is_active=True).prefetch_related('aliases'))
    for b in brokers:
        for a in b.aliases.all():
            out[a.graphite_agency_name.strip()] = b
    return out, brokers


def _collected_per_broker(start, end, alias_map):
    """Roll the per-agency collected totals (already split motor/non-motor) up
    onto their canonical broker.

    This is the whole point of the alias table: Redhill exists in Graphite under
    both 'Hilrange' and 'Hildrage', Dynamic under three names. Summing per
    agency row would split one broker's commission across three lines.
    """
    per_agency = graphite_feed.collected_by_agency(start, end)
    totals, unmapped = {}, {}
    for agency, vals in per_agency.items():
        if svc.is_direct_channel(agency):
            continue  # Alpha Direct's own book earns nobody commission.
        broker = alias_map.get(agency)
        if broker is None:
            unmapped[agency] = vals
            continue
        cur = totals.setdefault(broker.id, {'motor': 0.0, 'non_motor': 0.0,
                                            'amount': 0.0, 'count': 0})
        cur['motor'] += vals['motor']
        cur['non_motor'] += vals['non_motor']
        cur['amount'] += vals['amount']
        cur['count'] += vals['count']
    return totals, unmapped


def payables(period: str):
    """{broker_id: BrokerCommission} for every active broker — Decimals, for
    the month close to snapshot. None when no rates are configured."""
    start, end = month_bounds(period)
    rate_row = BrokerCommissionRate.in_force_on(start)
    if rate_row is None:
        return None
    rates = Rates.from_model(rate_row)
    alias_map, brokers = _alias_map()
    collected, _ = _collected_per_broker(start, end, alias_map)
    zero = {'motor': 0.0, 'non_motor': 0.0}
    return {b.id: compute(collected.get(b.id, zero)['motor'],
                          collected.get(b.id, zero)['non_motor'],
                          rates, withholding=b.withholding_tax)
            for b in brokers}


def build(period: str) -> dict:
    """The whole summary for one month. Pure read; safe to call repeatedly."""
    start, end = month_bounds(period)
    prev = previous_period(period)
    prev_start, prev_end = month_bounds(prev)

    rate_row = BrokerCommissionRate.in_force_on(start)
    health = graphite_feed.outcome_feed_health(start, end)
    alias_map, brokers = _alias_map()

    this_month, unmapped = _collected_per_broker(start, end, alias_map)
    last_month, _ = _collected_per_broker(prev_start, prev_end, alias_map)

    rates = Rates.from_model(rate_row) if rate_row else None
    rows = []
    # C5 — Previous Payable is last month's payable AS CAPTURED at its close
    # (including any logged late-resolution change), not a re-computation.
    snapshots = {c.broker_id: c.current_payable
                 for c in BrokerMonthClose.objects.filter(period=prev)}
    # C7 — the manual Compliance field, maintained by the Full Access role.
    compliance = {c.broker_id: c.compliance
                  for c in BrokerCompliance.objects.filter(period=period)}
    closed = BrokerMonthClose.objects.filter(period=period).order_by('created_at').first()

    for b in brokers:
        cur = this_month.get(b.id, {'motor': 0.0, 'non_motor': 0.0, 'amount': 0.0, 'count': 0})
        prv = last_month.get(b.id, {'motor': 0.0, 'non_motor': 0.0, 'amount': 0.0, 'count': 0})
        row = {
            'broker_id': str(b.id),
            'broker': b.name,
            'collected_gross': float(money(cur['amount'])),
            'motor_collected': float(money(cur['motor'])),
            'non_motor_collected': float(money(cur['non_motor'])),
            'collected_count': cur['count'],
            'previous_collected': float(money(prv['amount'])),
            'withholding_tax': b.withholding_tax,
            'commission_available': False,
            'blocked_reason': '',
            'compliance': compliance.get(b.id, ''),
            'previous_payable_source': 'closed' if b.id in snapshots else 'live',
        }
        if b.id in snapshots:
            row['previous_payable'] = float(snapshots[b.id])
        if rates is None:
            row['blocked_reason'] = 'No commission rates are configured for this month'
        else:
            calc = compute(cur['motor'], cur['non_motor'], rates, withholding=b.withholding_tax)
            prev_calc = compute(prv['motor'], prv['non_motor'], rates, withholding=b.withholding_tax)
            row.update(calc.as_dict())
            previous = snapshots.get(b.id, prev_calc.current_payable)
            row['previous_payable'] = float(previous)
            row['growth_pct'] = growth_pct(calc.current_payable, previous)
            row['commission_available'] = True
        rows.append(row)

    rows.sort(key=lambda r: (-r['collected_gross'], r['broker']))
    payable_rows = [r for r in rows if r['commission_available']]
    blocked = [{'broker': r['broker'], 'reason': r['blocked_reason']}
               for r in rows if not r['commission_available']]

    return {
        'period': period,
        'previous_period': prev,
        # Never a signed-off figure: see the module docstring.
        'preview': True,
        'signed_off': False,
        'preview_note': ('PREVIEW — not reconciled. The maths has not been checked '
                         'against a Finance workbook and no figure here has been '
                         'approved for payment.'),
        'rates': ({'motor_pct': float(rate_row.motor_pct),
                   'non_motor_pct': float(rate_row.non_motor_pct),
                   'vat_pct': float(rate_row.vat_pct),
                   'admin_pct': float(rate_row.admin_pct),
                   'wht_pct': float(rate_row.wht_pct),
                   'effective_from': rate_row.effective_from.isoformat()}
                  if rate_row else None),
        'rates_configured': rate_row is not None,
        'month_closed': closed is not None,
        'closed_at': closed.created_at.isoformat() if closed else None,
        'feed': health,
        # The single most important field on this response. When the outcome
        # feed is not reporting, every collected figure below is a floor, not a
        # total, and the screen must not present it as the month's collections.
        'figures_complete': bool(health.get('reporting')),
        'rows': rows,
        'blocked': blocked,
        'unmapped_agencies': sorted(unmapped.keys()),
        'totals': {
            'collected_gross': float(money(sum(r['collected_gross'] for r in rows))),
            'motor_collected': float(money(sum(r['motor_collected'] for r in rows))),
            'non_motor_collected': float(money(sum(r['non_motor_collected'] for r in rows))),
            'commission_excl_vat': float(money(sum(r.get('commission_excl_vat', 0)
                                                   for r in payable_rows))),
            'wht': float(money(sum(r.get('wht', 0) for r in payable_rows))),
            'vat': float(money(sum(r.get('vat', 0) for r in payable_rows))),
            'current_payable': float(money(sum(r.get('current_payable', 0)
                                               for r in payable_rows))),
            'brokers_with_commission': len(payable_rows),
            'brokers_blocked': len(blocked),
        },
    }
