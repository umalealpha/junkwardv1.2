"""fx/stuck.py — FX-rate loading as a stuck_work queue.

CFO brief 2026-08-26: stop the standalone "BoB FX rates not loaded" do-not-reply
email; instead raise an Omni task to Kago Tshutlhedi + Pako to load & approve the
day's Bank-of-Botswana rates, and auto-close it once the rates are approved.

The generic stuck_work engine (core/stuck_work.py) already gives exactly that:
a rolling OmniTask per person that refreshes daily and closes itself the moment
pending() returns empty (rates loaded + approved). CFO/EXCO are deliberately NOT
escalated — FX loading is a Finance-only chain (CFO directive 2026-06-17) — so
escalate_days is set out of reach.

The rate-freshness check mirrors the old guard (check_fx_rates_loaded.py) exactly
so the "loaded + approved" definition stays in one shape.
"""
from __future__ import annotations

import os

from core.stuck_work import PendingItem, Watcher

DEFAULT_TRACKED = ['USD', 'ZAR', 'GBP', 'EUR']
# The task goes to the two people the CFO brief named: Kago Tshutlhedi (FM) and
# Pako Kago (FC). By USERNAME so it never fans out to every finance-title holder
# (the QA bot omni@ + other FMs also carry the title) or to the wrong Pako.
FX_LOADER_USERNAMES = ['ktshutlhedi', 'pkago']
# Fallback only if neither named loader resolves (e.g. a role change): finance
# leadership by title.
FINANCE_TITLES = ['finance_manager', 'financial_controller']
FX_URL = 'https://omni.alphadirect.co.bw/settings/fx-rates'


def _tracked_currencies(ExchangeRate, Currency):
    """Currencies Finance maintains = those with prior BoB rates, else the majors
    that exist as Currency rows. .order_by() clears the model's default ordering
    which would otherwise leak into DISTINCT and duplicate codes."""
    tracked = sorted(set(
        ExchangeRate.objects.filter(source=ExchangeRate.Source.BANK_OF_BOTSWANA)
        .exclude(from_currency_id='BWP')
        .order_by().values_list('from_currency_id', flat=True)
    ))
    if not tracked:
        tracked = [c for c in DEFAULT_TRACKED if Currency.objects.filter(code=c).exists()]
    return tracked


def _fx_loaders():
    """The people the FX task lands on = Kago Tshutlhedi + Pako Kago (CFO brief).
    OMNI_FX_NOTIFY overrides for testing. If the two named loaders don't resolve
    (a role change), fall back to finance leadership by title, then the FM
    address — never the CFO (FX is a Finance-only chain)."""
    from django.contrib.auth.models import User
    from core.models import UserProfile

    env = (os.environ.get('OMNI_FX_NOTIFY') or '').strip()
    if env:
        emails = [e.strip() for e in env.split(',') if e.strip()]
        return tuple(User.objects.filter(email__in=emails, is_active=True))

    named = tuple(User.objects.filter(username__in=FX_LOADER_USERNAMES, is_active=True))
    if named:
        return named

    users = tuple(
        p.user for p in
        UserProfile.objects.filter(title__in=FINANCE_TITLES).select_related('user')
        if p.user and p.user.is_active and (p.user.email or '').strip()
    )
    if not users:
        fb = User.objects.filter(
            email__iexact='omogomotsi@alphadirect.co.bw', is_active=True).first()
        users = (fb,) if fb else ()
    return users


def _pending():
    """One PendingItem while today's BoB rates are missing/unapproved; [] once all
    tracked rates are loaded AND approved (which auto-closes the rolling task)."""
    from django.utils import timezone
    from core.models import Currency, ExchangeRate

    today = timezone.localdate()
    if today.weekday() >= 5:      # BoB doesn't publish on weekends — no task
        return []

    tracked = _tracked_currencies(ExchangeRate, Currency)
    if not tracked:
        return []

    flagged = []
    for cur in tracked:
        approved = ExchangeRate.objects.filter(
            from_currency_id=cur, to_currency_id='BWP',
            effective_date=today, approved_by__isnull=False).exists()
        if approved:
            continue
        loaded = ExchangeRate.objects.filter(
            from_currency_id=cur, to_currency_id='BWP',
            effective_date=today).exists()
        flagged.append(f'{cur} ({"loaded, awaiting approval" if loaded else "not loaded"})')

    if not flagged:
        return []       # all loaded + approved -> task auto-closes

    return [PendingItem(
        ref="Today's Bank of Botswana FX rates",
        detail='Load + approve on the FX rates page: ' + ', '.join(flagged),
        since=today,
        owed_by=_fx_loaders(),
    )]


FX_WATCHER = Watcher(
    key='fx_rates',
    label='FX rate loading',
    task_title="Load & approve today's BoB FX rates",
    url=FX_URL,
    sla_days=0,                 # due the same day BoB publishes
    escalate_days=10_000,       # Finance-only chain — never escalate to the CFO
    pending=_pending,
)
