"""
import_aft_claims — Saturday AFT claims-paid importer (CFO/Tlamelo 2026-06-13).

Scans a drop folder for ADI_AFT_PmtRun_*.xlsx delivered in the last N days and
imports each one idempotently (see healthcare/aft_import.py). If none arrived in
the window, optionally emails Tlamelo + EXCO a "no file this week" flag — exactly
as she asked.

Cron (prod, after the ~01:00-02:00 Saturday delivery):
    15 3 * * 6  cd /opt/alpha-finance && docker compose --env-file /etc/alpha-finance/.env \
        exec -T backend python manage.py import_aft_claims --flag-if-missing --notify

The file currently arrives by EMAIL to Tlamelo; until the mailbox can be read
programmatically (Azure Mail.Read consent — pending), drop the weekly file into
AFT_DROP_DIR (default /opt/alpha-finance/inbound/aft) or keep using the Claims
upload tab. Either way the import is idempotent, so a manual + auto run can't
double-count.

    python manage.py import_aft_claims --file /path/to/ADI_AFT_PmtRun_20260606.xlsx
    python manage.py import_aft_claims --dir /opt/alpha-finance/inbound/aft --since-days 8
    python manage.py import_aft_claims --dir ... --dry-run
    python manage.py import_aft_claims --dir ... --flag-if-missing --notify
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

_FLAG_TO = ['tchimidza@alphadirect.co.bw']
_PATTERN_HINTS = ('aft_pmtrun', 'pmtrun', 'adi_aft_pmt')


class Command(BaseCommand):
    help = 'Import weekly AFT claims-paid files idempotently; flag if none arrived.'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='Import one specific xlsx.')
        parser.add_argument('--dir', help='Drop folder to scan (default settings.AFT_DROP_DIR).')
        parser.add_argument('--since-days', type=int, default=8,
                            help='Only consider files modified in the last N days.')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--flag-if-missing', action='store_true',
                            help='Email Tlamelo + EXCO if no file arrived in the window.')
        parser.add_argument('--notify', action='store_true',
                            help='Email a per-run summary after each successful import.')

    def handle(self, *args, **opts):
        from healthcare.aft_import import import_aft_run

        files: list[Path] = []
        if opts.get('file'):
            p = Path(opts['file']).expanduser()
            if not p.exists():
                self.stderr.write(f'No such file: {p}')
                return
            files = [p]
        else:
            drop = Path(opts.get('dir') or getattr(settings, 'AFT_DROP_DIR',
                        '/opt/alpha-finance/inbound/aft')).expanduser()
            cutoff = time.time() - opts['since_days'] * 86400
            if drop.exists():
                for f in sorted(drop.glob('*.xlsx')):
                    nl = f.name.lower()
                    if any(h in nl for h in _PATTERN_HINTS) and f.stat().st_mtime >= cutoff:
                        files.append(f)
            self.stdout.write(f'Drop folder: {drop} | matching files in last '
                              f'{opts["since_days"]}d: {len(files)}')

        if not files:
            self.stdout.write(self.style.WARNING('No AFT file to import this window.'))
            if opts['flag_if_missing'] and not opts['dry_run']:
                self._flag_missing(opts['since_days'])
            return

        reports = []
        for p in files:
            blob = p.read_bytes()
            rep = import_aft_run(blob, p.name, dry_run=opts['dry_run'])
            reports.append((p.name, rep))
            self.stdout.write(
                f"  {p.name}: {rep['status']} | remit={rep.get('remit_date')} "
                f"paid={rep.get('total_paid')} lines={rep.get('line_count')}"
            )
            if opts['notify'] and not opts['dry_run'] and rep['status'] == 'imported':
                self._notify_run(p.name, rep)

        self.stdout.write(self.style.SUCCESS(f'Done — {len(reports)} file(s) processed.'))

    # --- notifications -------------------------------------------------------
    def _flag_missing(self, days):
        try:
            from core.notifications import send_html_with_cfo_cc
            send_html_with_cfo_cc(
                subject='[Omni] No AFT claims file this week',
                html=(
                    "<p>Heads-up: the weekly <b>AFT claims-payment file</b> "
                    f"(ADI_AFT_PmtRun_*.xlsx) has <b>not</b> arrived in the last {days} days, "
                    "so the Healthcare <b>claims-paid</b> figure was not refreshed this week.</p>"
                    "<p>If the run did happen, drop the file in the AFT folder or upload it on "
                    "the Claims tab and omni will import it (it won't double-count).</p>"
                    "<p>— omni / Finance Systems</p>"
                ),
                to=_FLAG_TO,
            )
            self.stdout.write('Flagged missing file by email.')
        except Exception as e:    # noqa: BLE001
            self.stderr.write(f'Could not send missing-file flag: {e}')

    def _notify_run(self, fname, rep):
        try:
            from core.notifications import send_html_with_cfo_cc
            ai = rep.get('ai_summary') or ''
            ai_block = f"<p style='background:#FAF7F2;border:1px solid #e5e7eb;border-radius:6px;padding:10px;'>{ai}</p>" if ai else ''
            send_html_with_cfo_cc(
                subject=f'[Omni] AFT claims imported — remit {rep.get("remit_date")}',
                html=(
                    f"<p>Imported <code>{fname}</code> for remit date <b>{rep.get('remit_date')}</b>.</p>"
                    f"<p>Total claims paid: <b>BWP {rep.get('total_paid')}</b> across "
                    f"{rep.get('line_count')} claim lines."
                    + (f" Superseded {rep.get('superseded_prior')} earlier import(s) for the same date." if rep.get('superseded_prior') else "")
                    + "</p>" + ai_block +
                    "<p>Nothing is live until you compare it against the source and approve it on the dashboard.</p>"
                    "<p>— omni / Finance Systems</p>"
                ),
                to=_FLAG_TO,
            )
        except Exception as e:    # noqa: BLE001
            self.stderr.write(f'Could not send run summary: {e}')
