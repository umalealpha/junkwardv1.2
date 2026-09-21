"""
hris/management/commands/workforce_hours_report.py

Month-end view for the CFO: per employee, how many hours were required,
tracked, justified and UNjustified for a month. Justified hours count as
working; unjustified count as non-working.

  python manage.py workforce_hours_report --month 2026-07
  python manage.py workforce_hours_report --month 2026-07 --csv out.csv

Reads the WorkdayJustification rows written by send_daily_brief.
"""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from hris.models import WorkdayJustification


class Command(BaseCommand):
    help = 'Per-employee justified vs unjustified hours for a month.'

    def add_arguments(self, parser):
        parser.add_argument('--month', required=True, help='Month to report, YYYY-MM.')
        parser.add_argument('--csv', dest='csv_path', help='Also write the table to this CSV file.')

    def handle(self, *args, **opts):
        try:
            year, month = (int(x) for x in opts['month'].split('-'))
            first = datetime.date(year, month, 1)
        except Exception:
            raise CommandError('--month must be YYYY-MM, e.g. 2026-07')
        last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)

        rows = (WorkdayJustification.objects
                .select_related('profile__employee')
                .filter(work_date__gte=first, work_date__lte=last)
                .order_by('profile__employee__full_name', 'work_date'))

        agg: dict = {}
        for r in rows:
            name = getattr(r.profile.employee, 'full_name', str(r.profile_id))
            a = agg.setdefault(name, {'required': Decimal('0'), 'tracked': Decimal('0'),
                                      'justified_days': 0, 'unjustified_days': 0, 'met_days': 0,
                                      'explained_days': 0})
            a['required'] += (r.required_hours or Decimal('0'))
            a['tracked']  += (r.tracked_hours or Decimal('0'))
            if r.status == WorkdayJustification.Status.JUSTIFIED:
                a['justified_days'] += 1
            elif r.status == WorkdayJustification.Status.UNJUSTIFIED:
                a['unjustified_days'] += 1
            elif r.status == WorkdayJustification.Status.MET:
                a['met_days'] += 1
            elif r.status == WorkdayJustification.Status.EXPLAINED:
                # Self-reported, awaiting manager sign-off — counted separately,
                # never silently folded into justified.
                a['explained_days'] += 1

        header = ['Employee', 'Required h', 'Tracked h', 'Met days', 'Justified days',
                  'Unjustified days', 'Pending review']
        self.stdout.write(' | '.join(header))
        table = []
        for name, a in agg.items():
            line = [name, f"{a['required']}", f"{a['tracked']}",
                    a['met_days'], a['justified_days'], a['unjustified_days'], a['explained_days']]
            table.append(line)
            self.stdout.write(' | '.join(str(x) for x in line))

        if not agg:
            self.stdout.write(self.style.WARNING('No workday records for that month yet.'))

        if opts.get('csv_path'):
            with open(opts['csv_path'], 'w', newline='', encoding='utf-8') as fh:
                w = csv.writer(fh)
                w.writerow(header)
                w.writerows(table)
            self.stdout.write(self.style.SUCCESS(f"Wrote {opts['csv_path']}."))
