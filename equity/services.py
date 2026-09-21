"""equity/services.py — shared equity calculations.

Kept in one place so the register API, the My-Equity page and the PDF statement
all price and vest a grant identically.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from reporting import equity_data
from django.utils import timezone


def current_share_price_usd() -> float:
    """Indicative price per fully-diluted share, on the same basis the dilution
    modeller uses: pre-money valuation ÷ current fully-diluted shares.

    This is a valuation-model figure, NOT a market price — the My-Equity page
    labels it as indicative so no one reads it as cash in hand.
    """
    fd = equity_data.SCENARIO.get('current_fd_shares') or 0
    pre = equity_data.SCENARIO.get('pre_money_usd') or 0
    if not fd:
        return 0.0
    return pre / fd


def vesting_summary(grant) -> dict:
    """Vested/unvested split for a grant as at today, from its tranches.

    Returns pct_vested = None when no schedule has been loaded, so callers can
    say 'per your letter of grant' rather than implying 0% vested.
    """
    today = timezone.localdate()
    tranches = list(grant.tranches.all())
    total_units = grant.units or 0

    if not tranches:
        return {
            'has_schedule': False,
            'vested_units': None,
            'unvested_units': None,
            'pct_vested': None,
            'next_vest_date': None,
            'next_vest_units': None,
        }

    vested = sum(t.units for t in tranches if t.vest_date <= today)
    unvested = sum(t.units for t in tranches if t.vest_date > today)
    upcoming = [t for t in tranches if t.vest_date > today]
    nxt = upcoming[0] if upcoming else None
    denom = total_units or (vested + unvested) or 1
    return {
        'has_schedule': True,
        'vested_units': vested,
        'unvested_units': unvested,
        'pct_vested': round(vested / denom * 100, 1),
        'next_vest_date': nxt.vest_date.isoformat() if nxt else None,
        'next_vest_units': nxt.units if nxt else None,
    }


def worth_usd(units: int) -> float:
    """Indicative value of a number of option units at the current share price."""
    return round((units or 0) * current_share_price_usd(), 2)


def generate_standard_tranches(grant, cliff_months: int = 12,
                               total_months: int = 48) -> list[dict]:
    """A market-standard 4-year monthly schedule with a 1-year cliff, computed
    from the grant's grant_date. Returned as plain dicts for Finance to REVIEW
    and confirm — this helper never writes to the database on its own, because
    the real schedule lives in each Letter of Grant, not in a default.
    """
    if not grant.grant_date or not grant.units:
        return []
    start = grant.grant_date
    units = grant.units
    per_month = units // total_months
    out = []
    allocated = 0
    for m in range(cliff_months, total_months + 1):
        # month offset m from start
        y = start.year + (start.month - 1 + m) // 12
        mo = (start.month - 1 + m) % 12 + 1
        day = min(start.day, 28)
        d = datetime.date(y, mo, day)
        if m == cliff_months:
            u = per_month * cliff_months  # cliff releases the first year at once
        elif m == total_months:
            u = units - allocated  # last tranche mops up rounding
        else:
            u = per_month
        allocated += u
        out.append({'vest_date': d.isoformat(), 'units': u})
    return out
