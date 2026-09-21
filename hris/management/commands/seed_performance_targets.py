"""
Seed standing monthly performance targets from job descriptions (CFO 2026-07-20).

Idempotent. The CFO's worked example: Gosego Makone (Health Insurance Associate)
= BWP 30,000 new sales/month, auto-verified from omni's own health quotes. HR
extends this roster as job descriptions are captured; re-run any time.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

# (name-match, metric, target_value, unit, source, note)
ROSTER = [
    ('Gosego Makone', 'New sales', Decimal('30000'), 'BWP', 'health_quotes',
     'Per job description: BWP 30,000 group-health new sales per month.'),
]


class Command(BaseCommand):
    help = "Seed monthly performance targets from job descriptions (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Write (else dry-run).')

    def handle(self, *args, **opts):
        from hris.models import HRISProfile
        from hris.performance_target_models import PerformanceTarget

        commit = opts['commit']
        created = matched = missing = 0
        for name, metric, value, unit, source, note in ROSTER:
            prof = (HRISProfile.objects.select_related('employee')
                    .filter(employee__full_name__iexact=name).first())
            if prof is None:
                prof = (HRISProfile.objects.select_related('employee')
                        .filter(employee__full_name__icontains=name).first())
            if prof is None:
                self.stdout.write(f"  ! no employee matched '{name}' — skipped")
                missing += 1
                continue
            existing = PerformanceTarget.objects.filter(profile=prof, metric__iexact=metric).first()
            if existing:
                matched += 1
                self.stdout.write(f"  = {name}: '{metric}' already set")
                continue
            self.stdout.write(f"  + {name}: '{metric}' {value} {unit} ({source})")
            if commit:
                PerformanceTarget.objects.create(
                    profile=prof, metric=metric, target_value=value,
                    unit=unit, source=source, note=note, active=True)
            created += 1
        self.stdout.write(self.style.SUCCESS(
            f"targets created={created} already={matched} missing={missing}"
            + ('' if commit else '  [DRY RUN — pass --commit]')))
