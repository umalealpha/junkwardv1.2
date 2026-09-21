"""Seed a new joiner's annual-leave opening balance at onboarding.

The gap (Oprah Mogomotsi, bug c48f10b0, 2026-09-03): the live accrual engine in
``hris.leave_balance`` only accrues an employee forward when a
``LeaveOpeningBalance`` row exists for them — otherwise the Leave Report shows
zero entitlement and zero accrued until HR does a manual bulk CSV upload. So a
freshly onboarded person (e.g. Unopa Male, started 3 Sep) sat at 0/0.

Fix: when an onboarding request is approved, give the person an ``annual``
opening-balance row (``as_at_date`` = their start date, opening 0). The tested
opening-balance path then accrues 1/12 per completed month from day one — no
CSV needed. The entitlement HR captured on the onboarding form drives it; blank
falls back to the Conditions-of-Service annual default.

This module never touches ``leave_balance`` itself — it only writes the row that
the existing engine already knows how to read.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.utils import timezone

from hris.models import LeaveOpeningBalance

# CoS §7.1 / ELRA s.219 statutory annual entitlement, used when HR leaves the
# onboarding field blank. Mirrors get_leave_rules()['annual']['days'].
DEFAULT_ANNUAL_DAYS = Decimal('21')

_ANNUAL = 'annual'


def _as_decimal(value) -> Decimal | None:
    """Parse a days value, or None when there is nothing usable to parse.

    None is a deliberate sentinel, not a swallowed error: every caller treats
    None as "no entitlement given" and falls back to the CoS default, so a blank
    or malformed field never blocks onboarding — it just uses the standard 21.
    """
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def seed_annual_opening(profile, as_at_date, entitlement_days, actor=None):
    """Create the ``annual`` opening-balance row for a newly onboarded profile.

    Idempotent and non-destructive: if the profile already has ANY annual
    opening-balance row (an earlier seed, or a corrective HR upload) this leaves
    it untouched and returns ``None`` — the upload/earlier figure always wins.
    Returns the created row otherwise.
    """
    if profile is None:
        return None
    if LeaveOpeningBalance.objects.filter(
            profile=profile, leave_type_code=_ANNUAL).exists():
        return None

    ent = _as_decimal(entitlement_days)
    if ent is None or ent <= 0:
        ent = DEFAULT_ANNUAL_DAYS

    return LeaveOpeningBalance.objects.create(
        profile=profile,
        leave_type_code=_ANNUAL,
        as_at_date=as_at_date or timezone.localdate(),
        entitlement_days=ent,
        opening_balance_days=Decimal('0'),
        accrued_days=Decimal('0'),
        batch='onboarding',
        uploaded_by=actor if getattr(actor, 'pk', None) else None,
    )


def set_annual_entitlement(profile, entitlement_days, actor=None):
    """Change an employee's annual entitlement after onboarding (Amend Profile).

    Updates the entitlement on the profile's latest annual opening-balance row,
    or creates one (as at the employee's start date, or today) if none exists.
    Accrual then recomputes at the new entitlement from that row's as-at date —
    the natural "their entitlement changed" behaviour. Returns the row.
    """
    if profile is None:
        return None
    ent = _as_decimal(entitlement_days)
    if ent is None or ent < 0:
        raise ValueError('Annual leave entitlement must be a non-negative number of days.')

    row = (LeaveOpeningBalance.objects
           .filter(profile=profile, leave_type_code=_ANNUAL)
           .order_by('-as_at_date', '-created_at')
           .first())
    if row is not None:
        row.entitlement_days = ent
        row.save(update_fields=['entitlement_days'])
        return row

    start = getattr(getattr(profile, 'employee', None), 'hire_date', None)
    return LeaveOpeningBalance.objects.create(
        profile=profile,
        leave_type_code=_ANNUAL,
        as_at_date=start or timezone.localdate(),
        entitlement_days=ent,
        opening_balance_days=Decimal('0'),
        accrued_days=Decimal('0'),
        batch='amend-profile',
        uploaded_by=actor if getattr(actor, 'pk', None) else None,
    )
