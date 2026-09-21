"""Generate the weekly Excel snapshots of the Aware canned reports.

Scheduled Sunday 06:00 SAST by the host cron entry `infra/cron/aware-reports.cron`.
On-demand:  python manage.py generate_aware_reports
            python manage.py generate_aware_reports --dry-run

Writes <MEDIA_ROOT>/aware_reports/<key>_<YYYY-MM-DD>.xlsx for each report and
keeps the most recent KEEP_WEEKS files per report (so staff can download past
weeks to reconcile). Files live on the persistent media volume, so they
survive container rebuilds/deploys.
"""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from aware import reporting
from aware.reporting import reports_dir
from aware.modes.broker_analysis import broker_report
from aware.modes.claims_registry import claims_registry_report
from aware.modes.top_dom import top_domestic_report

KEEP_WEEKS = 26

# key -> (report function, xlsx assembler)
REPORTS = {
    'broker-analysis': (broker_report, reporting.broker_xlsx),
    'top-50-dom': (top_domestic_report, reporting.top_dom_xlsx),
    'claims-registry': (claims_registry_report, reporting.claims_xlsx),
}


class Command(BaseCommand):
    help = 'Generate the weekly Excel snapshots of the Aware canned reports.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Build in memory + print, but do not write files.')

    def handle(self, *args, **opts):
        stamp = timezone.now().strftime('%Y-%m-%d')
        out = reports_dir()
        for key, (fn, xlsx) in REPORTS.items():
            try:
                data = fn()
                buf = xlsx(data, stamp)
            except Exception as e:  # one bad report must not kill the others
                self.stderr.write(f'{key}: FAILED — {e}')
                continue
            size = len(buf.getbuffer())
            if opts['dry_run']:
                self.stdout.write(f'{key}: built {size:,} bytes (dry-run, not written)')
                continue
            path = out / f'{key}_{stamp}.xlsx'
            path.write_bytes(buf.getvalue())
            self._prune(out, key)
            self.stdout.write(self.style.SUCCESS(f'{key}: wrote {path.name} ({size:,} bytes)'))

    def _prune(self, out: Path, key: str):
        files = sorted(out.glob(f'{key}_*.xlsx'), reverse=True)
        for old in files[KEEP_WEEKS:]:
            try:
                old.unlink()
            except OSError:
                pass
