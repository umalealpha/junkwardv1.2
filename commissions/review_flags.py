"""commissions/review_flags.py — at-a-glance sanity checks a reviewer sees on
each commission BEFORE approving (CFO 2026-08-22, "make the checkers' life
better", phase 1).

Deterministic ONLY — no AI, no cost, no false-alarm-prone guesses. Each flag is
a plain-English sentence a non-accountant reads once. `level` is 'clean' when
nothing is worth a second look, else 'check'. Best-effort at the call site: a
failure here must never break the review list.

Checks:
  * total ties — gross_commission equals the sum of the line commissions
  * duplicate policy — the same policy number appears on more than one line
  * zero-commission line — a line names a policy but carries no commission
  * rate vs commission — a line's commission is far from rate x premium
    (checked under BOTH "rate is a %" and "rate is a fraction", flagged only
    when NEITHER fits — so the %/fraction ambiguity never cries wolf)
  * month-on-month spike — gross is wildly different from this agent's last
    approved/paid month (catches a typo or a doubled sheet)
"""
from __future__ import annotations

from decimal import Decimal

# thresholds tuned to flag real problems, not noise
_SPIKE_PCT = Decimal('0.50')      # >=50% change vs last month
_RATE_ABS = Decimal('5.00')       # ignore rate mismatches under P5
_RATE_PCT = Decimal('0.05')       # ...and under 5% of the line
_MAX_ITEMS = 6


def _money(v) -> str:
    return 'P' + f'{Decimal(v or 0):,.2f}'


def _prior_gross(sub):
    """This agent's most recent APPROVED/PAID gross before this month, or None."""
    from .models import CommissionSubmission
    S = CommissionSubmission.Status
    prev = (CommissionSubmission.objects
            .filter(agent_id=sub.agent_id,
                    status__in=[S.APPROVED, S.PAID],
                    period_label__lt=sub.period_label)
            .order_by('-period_label')
            .first())
    return prev.gross_commission if prev else None


def review_flags(sub) -> dict:
    """{'level': 'clean'|'check', 'items': [str, ...]}. Never raises."""
    items: list[str] = []
    try:
        lines = list(sub.lines.all())

        # total ties
        line_sum = sum((l.commission_amount for l in lines), Decimal('0.00'))
        if (sub.gross_commission or Decimal('0.00')) != line_sum:
            items.append(f"Total {_money(sub.gross_commission)} doesn't match the "
                         f"lines added up ({_money(line_sum)})")

        # duplicate policy numbers
        seen, dups = set(), set()
        for l in lines:
            p = (l.policy_number or '').strip()
            if not p:
                continue
            (dups if p in seen else seen).add(p)
        for p in sorted(dups):
            items.append(f"Policy {p} appears on more than one line")

        # zero-commission line + rate-vs-commission
        for l in lines:
            p = (l.policy_number or '').strip()
            comm = l.commission_amount or Decimal('0.00')
            if p and comm == 0:
                items.append(f"Policy {p} has no commission (P0.00)")
            ac = l.amount_applicable or Decimal('0.00')
            rate = l.commission_rate or Decimal('0.00')
            if p and ac > 0 and rate > 0 and comm > 0:
                exp_pct = ac * rate / Decimal('100')   # rate entered as 12.5
                exp_frac = ac * rate                   # rate entered as 0.125
                err = min(abs(comm - exp_pct), abs(comm - exp_frac))
                if err > _RATE_ABS and err > comm * _RATE_PCT:
                    closer = exp_pct if abs(comm - exp_pct) <= abs(comm - exp_frac) else exp_frac
                    items.append(f"Policy {p}: commission {_money(comm)} doesn't match "
                                 f"rate x premium (about {_money(closer)})")

        # month-on-month spike
        prior = _prior_gross(sub)
        if prior and prior > 0:
            change = (sub.gross_commission - prior) / prior
            if abs(change) >= _SPIKE_PCT:
                arrow = 'up' if change > 0 else 'down'
                items.append(f"Gross {_money(sub.gross_commission)} is {abs(change) * 100:.0f}% "
                             f"{arrow} vs last month ({_money(prior)})")
    except Exception:
        import logging
        logging.getLogger('commissions').info('review_flags failed for %s',
                                               getattr(sub, 'id', '?'), exc_info=True)
        # 'unknown', NOT 'clean' — a crashed check must never read as "all good".
        # The badge then shows nothing rather than a false green tick (H6).
        return {'level': 'unknown', 'items': []}

    items = items[:_MAX_ITEMS]
    return {'level': 'check' if items else 'clean', 'items': items}
