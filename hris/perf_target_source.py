"""
hris/perf_target_source.py — obtain the ACTUAL for a monthly target.

Only ONE automated source in v1: group-health new sales, read from omni's own
`healthcare.HealthQuote` (no Graphite, no PII leaves omni). Everything else is
manual (the manager types the actual). Generic Graphite new-business is NOT
mappable to an internal staff member yet (no Employee↔Graphite-user link), so it
stays manual until that mapping exists.
"""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Q, Sum


def month_date_window(year: int, month: int):
    """(first_day, last_day) as dates for the given month."""
    first = dt.date(year, month, 1)
    last  = dt.date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def resolve_user(emp) -> User | None:
    """payroll.Employee → auth.User (FK first, then email match)."""
    if emp is None:
        return None
    u = getattr(emp, 'user', None)
    if u is not None:
        return u
    email = (getattr(emp, 'email', '') or '').strip()
    return User.objects.filter(email__iexact=email).first() if email else None


def _health_new_sales(user: User, year: int, month: int) -> dict:
    """Group-health quotes this user closed (approved/invoiced) in the month, split
    into VAT-exclusive / VAT / VAT-inclusive (CFO 2026-07-20 — show VAT separately).

    The sale is credited to the month it became real (approved_at); if a quote was
    invoiced without a stamped approval time we fall back to created_at so it is
    counted once. Each quote is credited to ONE person — its creator, or the
    submitter only when there is no creator — never double-counted (Fable L).
    """
    from healthcare.models import HealthQuote

    first, last = month_date_window(year, month)
    owner = Q(created_by=user) | (Q(created_by__isnull=True) & Q(submitted_by=user))
    closed = (HealthQuote.objects.filter(owner)
              .filter(status__in=[HealthQuote.Status.APPROVED, HealthQuote.Status.INVOICED]))

    in_month = closed.filter(approved_at__date__gte=first, approved_at__date__lte=last)
    # quotes closed but with no approved_at stamp — attribute by created_at
    no_stamp = closed.filter(approved_at__isnull=True,
                             created_at__date__gte=first, created_at__date__lte=last)

    def _sum(qs):
        a = qs.aggregate(e=Sum('subtotal_excl'), v=Sum('vat'), i=Sum('total_incl'))
        return (a['e'] or Decimal('0'), a['v'] or Decimal('0'), a['i'] or Decimal('0'))

    e1, v1, i1 = _sum(in_month)
    e2, v2, i2 = _sum(no_stamp)
    return {'excl': e1 + e2, 'vat': v1 + v2, 'incl': i1 + i2}


def pull_actual(target, year: int, month: int):
    """Return (breakdown, source_used) for an auto source, else (None, '').
    breakdown = {'excl','vat','incl'} (Decimals). Never raises — a source error
    yields the manual fallback."""
    from hris.performance_target_models import PerformanceTarget

    if target.source == PerformanceTarget.Source.HEALTH_QUOTES:
        user = resolve_user(getattr(target.profile, 'employee', None))
        if user is None:
            return None, ''
        try:
            return _health_new_sales(user, year, month), 'health_quotes'
        except Exception:  # noqa: BLE001 — auto-pull must never break the panel
            return None, ''
    return None, ''


def is_achieved(target, actual) -> bool | None:
    """Did the actual meet/beat the target? None if there is no actual yet."""
    if actual is None:
        return None
    try:
        return Decimal(str(actual)) >= Decimal(str(target.target_value))
    except Exception:  # noqa: BLE001
        return None
