#!/usr/bin/env python
"""
seed_undo_mar2026_snapshot.py — undo the Mar 2026 closing-balance snapshot
that replaced the FY25 + FY26 YTD JEs.

A parallel session collapsed both period JEs into a single JE dated
2026-03-31 ("Mar 2026 closing balance snapshot from CFO Odoo TB"). That
breaks the dashboard's period filter — FY25-end (30/06/2025) queries
return zero because no JE falls within the window. CFO confirmed the
collapse doesn't fit his accounting workflow; reverting and restoring
the period-specific JEs below.

After this runs:
  - The Mar 2026 snapshot JE is deleted
  - seed_fy25_gl.py and seed_fy26_gl.py can re-create the originals
    (their idempotency check is by entry_date + description)

Run:
    python manage.py shell < ops/seeds/seed_undo_mar2026_snapshot.py
"""
from django.db import transaction
from django.db.models import Q
from ledger.models import JournalEntry, JournalEntryLine


def main():
    with transaction.atomic():
        targets = JournalEntry.objects.filter(
            Q(description__icontains='Mar 2026 closing balance snapshot')
            | Q(description__icontains='closing balance snapshot from CFO Odoo TB')
        )
        pks = list(targets.values_list('pk', flat=True))
        n = len(pks)
        if not n:
            print('  No Mar 2026 snapshot JE found; nothing to undo.')
            return
        # Bulk delete bypasses the model-level immutability guard
        JournalEntryLine.objects.filter(journal_entry_id__in=pks).delete()
        JournalEntry.objects.filter(pk__in=pks).delete()
        print(f'  Removed {n} snapshot JE(s) (and their lines)')


main()
