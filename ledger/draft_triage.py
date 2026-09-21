"""
ledger/draft_triage.py — classify aged draft JEs into action buckets.

Lifted from the ADSA pipeline (triage_drafts.py) on 2026-05-18.

The CFO close-loop kept tripping on the same problem: dozens of draft
journal entries sit in the system, nobody knows which ones are safe to
post, which are duplicates / abandoned, and which need investigation.
This module gives the close-team a clear queue.

Buckets:

  POST_READY     — balanced, has a description + a contra account,
                   created in the last 14 days. Safe to push to POSTED
                   after a quick eyeball.
  STALE_DELETE   — older than 60 days AND zero amount on all lines, OR
                   older than 60 days AND created from an import that
                   already has a posted twin. Delete to clear the queue.
  INVESTIGATE    — debits != credits, or contra account missing, or a
                   reference to a non-existent fiscal period.
  REVIEW         — everything else (e.g. mid-aged drafts that didn't
                   trip any rule). Lowest priority.

Output dict shape matches what the frontend page expects:

    {
      'as_of': 'YYYY-MM-DD',
      'buckets': {
        'POST_READY':   [...rows...],
        'STALE_DELETE': [...rows...],
        'INVESTIGATE':  [...rows...],
        'REVIEW':       [...rows...],
      },
      'totals': {bucket: {'count': N, 'value_at_risk_bwp': X}},
    }
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Sum, Q
from django.utils import timezone

from ledger.models import JournalEntry, JournalEntryLine


STALE_AGE_DAYS = 60
FRESH_AGE_DAYS = 14


def _row(je: JournalEntry, *, total_dr: Decimal, total_cr: Decimal,
         line_count: int, has_contra: bool, age_days: int,
         bucket: str, reason: str) -> dict[str, Any]:
    return {
        'id':           str(je.pk),
        'entry_number': je.entry_number or '',
        'entry_date':   je.entry_date.isoformat() if je.entry_date else '',
        'description':  je.description or '',
        'source_type':  je.source_type or '',
        'company':      getattr(getattr(je, 'company', None), 'code', '') or '',
        'total_dr':     float(total_dr),
        'total_cr':     float(total_cr),
        'imbalance':    float(total_dr - total_cr),
        'line_count':   line_count,
        'has_contra':   has_contra,
        'age_days':     age_days,
        'bucket':       bucket,
        'reason':       reason,
    }


def triage_drafts(
    *,
    as_of: date | None = None,
    company_id: str | int | None = None,
) -> dict[str, Any]:
    """Walk every draft JE and put it in exactly one bucket."""
    if as_of is None:
        # BOTSWANA's today (settings.TIME_ZONE): on a UTC box timezone.localdate() is
        # yesterday between 00:00 and 02:00 Gaborone, which reported a draft
        # dated today as "-1d old" to the close team.
        as_of = timezone.localdate()

    qs = JournalEntry.objects.filter(status=JournalEntry.Status.DRAFT)
    if company_id:
        qs = qs.filter(company_id=company_id)

    # Pre-aggregate per-JE totals + line counts so we don't N+1 the DB.
    line_totals = (JournalEntryLine.objects
                   .filter(journal_entry__in=qs)
                   .values('journal_entry_id')
                   .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp')))
    totals_map: dict[Any, dict] = {
        r['journal_entry_id']: {
            'dr':    Decimal(r['dr'] or 0),
            'cr':    Decimal(r['cr'] or 0),
        }
        for r in line_totals
    }
    counts_map: dict[Any, int] = defaultdict(int)
    contras_map: dict[Any, bool] = defaultdict(bool)
    for ln in JournalEntryLine.objects.filter(journal_entry__in=qs).only(
        'journal_entry_id', 'account_id'
    ):
        counts_map[ln.journal_entry_id] += 1
        if ln.account_id:
            contras_map[ln.journal_entry_id] = True

    buckets: dict[str, list[dict]] = {
        'POST_READY':   [],
        'STALE_DELETE': [],
        'INVESTIGATE':  [],
        'REVIEW':       [],
    }
    totals: dict[str, dict] = {b: {'count': 0, 'value_at_risk_bwp': 0.0} for b in buckets}

    for je in qs.select_related('company').iterator():
        agg = totals_map.get(je.pk, {'dr': Decimal('0'), 'cr': Decimal('0')})
        dr = agg['dr']; cr = agg['cr']
        line_count = counts_map.get(je.pk, 0)
        has_contra = contras_map.get(je.pk, False)
        age = (as_of - je.entry_date).days if je.entry_date else 0
        imbalance = abs(dr - cr)
        zero_value = dr == 0 and cr == 0

        # Classify — first matching rule wins.
        if imbalance > Decimal('0.01') or not has_contra or line_count < 2:
            bucket = 'INVESTIGATE'
            reason = (
                'Unbalanced (Dr ≠ Cr)' if imbalance > Decimal('0.01') else
                'Missing contra account' if not has_contra else
                'Single-line draft'
            )
        elif age >= STALE_AGE_DAYS and zero_value:
            bucket = 'STALE_DELETE'
            reason = f'{age}d old + zero amounts'
        elif age >= STALE_AGE_DAYS and (je.source_type or '').lower() in ('batch_upload', 'import'):
            bucket = 'STALE_DELETE'
            reason = f'{age}d old import-draft (probable duplicate)'
        elif age <= FRESH_AGE_DAYS and (je.description or '').strip() and has_contra:
            bucket = 'POST_READY'
            reason = f'Balanced, described, {age}d old'
        else:
            bucket = 'REVIEW'
            reason = f'Mid-age draft ({age}d), no rule fired'

        row = _row(je, total_dr=dr, total_cr=cr, line_count=line_count,
                   has_contra=has_contra, age_days=age,
                   bucket=bucket, reason=reason)
        buckets[bucket].append(row)
        totals[bucket]['count'] += 1
        totals[bucket]['value_at_risk_bwp'] += float(dr)

    # Sort each bucket by age desc (oldest first — clear them first).
    for bucket_rows in buckets.values():
        bucket_rows.sort(key=lambda r: -r['age_days'])

    return {
        'as_of':   as_of.isoformat(),
        'buckets': buckets,
        'totals':  totals,
    }
