"""
staff_loans/rates.py

The scheme interest rate = Bank of Botswana Monetary Policy Rate (MoPR) + a
spread. The MoPR is the reliably-published public rate; commercial banks price
personal loans off it. A monthly cron reads it and stores a new StaffLoanRate
row. Everything here is fail-safe: if the public rate can't be read, the last
rate is kept and Finance is told to set it by hand (it only moves a few times
a year, on a BoB MPC decision).
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from django.utils import timezone

from . import policy

log = logging.getLogger(__name__)

# The BoB rates page publishes the MoPR (and Bank/7-day/BoBC rates). It does
# NOT publish a commercial "prime" — the MoPR is the clean public anchor.
BOB_RATES_URL = 'https://www.bankofbotswana.bw/content/interest-rates'
SANE_MIN = Decimal('0.5')     # a public policy rate below this is a parse error
SANE_MAX = Decimal('20')      # ...or above this


def current_rate_row():
    from .models import StaffLoanRate
    today = timezone.localdate()
    return (StaffLoanRate.objects.filter(effective_from__lte=today)
            .order_by('-effective_from', '-created_at').first()
            or StaffLoanRate.objects.order_by('-effective_from', '-created_at').first())


def current_annual_rate() -> Decimal:
    row = current_rate_row()
    return row.annual_rate_pct if row else policy.DEFAULT_ANNUAL_RATE_PCT


def current_spread() -> Decimal:
    row = current_rate_row()
    return row.spread_pct if row else policy.DEFAULT_SPREAD_PCT


def fetch_reference_rate(url: str = BOB_RATES_URL):
    """The BoB Monetary Policy Rate as Decimal, or None if it can't be read.

    Conservative on purpose: any HTTP/parse problem, or a number outside the
    sane band, returns None so the caller keeps the last known rate.
    """
    try:
        import requests
        resp = requests.get(url, timeout=20, headers={'User-Agent': 'alpha-finance/1.0 (staff-loan-rate)'})
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:  # noqa: BLE001
        log.warning('staff-loan MoPR fetch failed: %s', exc)
        return None
    m = re.search(r'Monetary Policy Rate[^0-9]{0,60}(\d{1,2}(?:\.\d{1,2})?)', html, re.I | re.S)
    if not m:
        log.warning('staff-loan MoPR not found in %s', url)
        return None
    val = Decimal(m.group(1))
    if not (SANE_MIN <= val <= SANE_MAX):
        log.warning('staff-loan MoPR %s outside sane band — ignoring', val)
        return None
    return val


def refresh(*, user=None, url: str = BOB_RATES_URL) -> dict:
    """Fetch the MoPR and upsert this month's auto rate row (= MoPR + spread).

    Never writes a bad rate: on a fetch/parse miss it leaves the rate unchanged
    and reports status 'fetch_failed'.
    """
    from .models import StaffLoanRate
    mopr = fetch_reference_rate(url)
    spread = current_spread()
    if mopr is None:
        return {'status': 'fetch_failed', 'kept': str(current_annual_rate())}

    rate = policy.q(mopr + spread)
    first_of_month = timezone.localdate().replace(day=1)
    row, created = StaffLoanRate.objects.update_or_create(
        source=StaffLoanRate.Source.AUTO, effective_from=first_of_month,
        defaults=dict(
            annual_rate_pct=rate,
            reference_name='Bank of Botswana Monetary Policy Rate',
            reference_rate_pct=mopr, spread_pct=spread, source_url=url,
            created_by=user, note=f'Auto: MoPR {mopr}% + spread {spread}%',
        ),
    )
    return {'status': 'created' if created else 'updated',
            'rate': str(rate), 'mopr': str(mopr), 'spread': str(spread)}
