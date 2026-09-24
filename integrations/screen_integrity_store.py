"""
integrations/screen_integrity_store.py

Persist one day's frozen-screen sweep so the CFO can PULL exceptions from the
Omni screen instantly (no live Time Doctor pull on page load). Written by the
daily cron (detect_frozen_screen --persist) and by the live "re-scan" button.

Privacy (AD-POL-AI-GOV-001): names + counts only — never a window title, image
or md5. `flagged()` already restricts persisted rows to watch/suspicious people.
"""
from __future__ import annotations

from datetime import date as _date
from decimal import Decimal
from typing import List

from django.db import transaction
from django.utils import timezone

from integrations.models import ScreenIntegrityScan, ScreenIntegrityFlag
from integrations.td_screenshot_integrity import flagged


def _dec(v) -> Decimal:
    return Decimal(str(round(float(v or 0), 2)))


@transaction.atomic
def persist_day(day: _date, sigs: List, *, status: str = ScreenIntegrityScan.Status.OK,
                note: str = '') -> ScreenIntegrityScan:
    """Upsert the per-day summary and replace that day's flag rows.

    Idempotent: re-scanning a day overwrites its previous result cleanly, so a
    manual re-run never double-counts and never leaves a stale flag behind.
    """
    flags = flagged(sigs or [])
    n_susp = sum(1 for s in flags if s.suspicion == 'suspicious')
    n_watch = sum(1 for s in flags if s.suspicion == 'watch')

    scan, _ = ScreenIntegrityScan.objects.update_or_create(
        day=day,
        defaults=dict(
            people_checked=len(sigs or []),
            suspicious=n_susp,
            watch=n_watch,
            status=status,
            note=(note or '')[:200],
            ran_at=timezone.now(),
        ),
    )
    # Replace the day's flags wholesale (idempotent re-scan).
    scan.flags.all().delete()
    ScreenIntegrityFlag.objects.bulk_create([
        ScreenIntegrityFlag(
            scan=scan,
            day=day,
            td_user_id=str(s.user_id or ''),
            name=s.name or '',
            suspicion=s.suspicion,
            shots=s.shots,
            frozen_typing_pct=_dec(round(s.frozen_typing_pct * 100, 1)),
            frozen_typing_hours=_dec(s.frozen_typing_hours),
            mouse_dead_pct=_dec(round(s.mouse_dead_pct * 100, 1)),
            identical_pct=_dec(round(s.identical_pct * 100, 1)),
            idle_frozen_pct=_dec(getattr(s, 'idle_frozen_pct', 0) * 100),
            idle_frozen_hours=_dec(getattr(s, 'idle_frozen_hours', 0)),
            reasons=list(s.reasons or []),
        )
        for s in flags
    ])
    return scan
