"""
refresh_ml_recoveries_cache — nightly cron to warm the recoveries summary
for the omni CFO dashboard tile.

Calls build_recoveries_summary() once per company over a 12-month rolling
window and writes the result to Django cache so the tile renders fast.

Run from cron / scheduled-tasks:

    python manage.py refresh_ml_recoveries_cache
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Warm the Motor Liquidators recoveries summary cache.'

    def add_arguments(self, parser):
        parser.add_argument('--months', type=int, default=12,
                            help='Rolling window in months (default 12).')

    def handle(self, *args, **opts):
        from core.models import Company
        from salvage.models import SalvageItem, Sale
        from django.db.models import Sum, Count

        months = opts['months']
        today  = timezone.localdate()
        from_d = today - timedelta(days=months * 30)

        companies = list(Company.objects.values_list('id', 'code'))
        out = {'as_of': today.isoformat(), 'from_date': from_d.isoformat(), 'rows': []}

        for cid, code in companies:
            si = SalvageItem.objects.filter(company_id=cid)
            sa = Sale.objects.filter(item__company_id=cid,
                                     sale_date__gte=from_d,
                                     sale_date__lte=today)
            total_si = si.aggregate(t=Sum('sum_insured'))['t'] or Decimal('0')
            total_rc = sa.aggregate(t=Sum('sale_price'))['t'] or Decimal('0')
            row = {
                'company_id':      str(cid),
                'company_code':    code,
                'sum_insured':     str(total_si),
                'recovered':       str(total_rc),
                'recovery_rate':   round(float(total_rc) / float(total_si) * 100, 2) if total_si else 0.0,
                'item_count':      si.count(),
                'sale_count':      sa.count(),
            }
            cache.set(f'ml_recoveries:{code}', row, timeout=24 * 3600)
            out['rows'].append(row)
            self.stdout.write(f"  {code:<8} SI={row['sum_insured']:>14}  REC={row['recovered']:>14}  rate={row['recovery_rate']:>6}%")

        cache.set('ml_recoveries:all', out, timeout=24 * 3600)
        self.stdout.write(self.style.SUCCESS(
            f"Cached recoveries for {len(out['rows'])} companies."
        ))
