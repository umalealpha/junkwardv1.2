"""Snapshot every table's row count and email the CFO if anything disappeared.

Run hourly from cron. Compares against the previous snapshot; on any qualifying drop
it emails, marks the snapshot as alerted, and exits 1 so a cron wrapper can notice.

    python manage.py check_row_counts              # normal hourly run
    python manage.py check_row_counts --dry-run    # count + compare, never write or send
    python manage.py check_row_counts --show 15    # print the biggest tables and stop
"""
from django.core.management.base import BaseCommand

from core.models import RowCountSnapshot
from core.row_watchdog import build_alert_html, current_counts, find_drops


class Command(BaseCommand):
    help = "Row-count watchdog: alert the CFO when data disappears from Omni."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='compare and report, but write nothing and send nothing')
        parser.add_argument('--show', type=int, default=0,
                            help='just print the N biggest tables and exit')
        parser.add_argument('--to', default='', help='override the alert recipient')

    def handle(self, *args, **opts):
        counts = current_counts()
        total = sum(counts.values())

        if opts['show']:
            for label, n in sorted(counts.items(), key=lambda kv: -kv[1])[:opts['show']]:
                self.stdout.write(f'  {label:52s} {n:>10,}')
            self.stdout.write(f'  {"TOTAL":52s} {total:>10,} rows in {len(counts)} tables')
            return

        previous = RowCountSnapshot.objects.order_by('-taken_at').first()
        if previous is None:
            # Nothing to compare against yet. Record the baseline and say so plainly
            # rather than reporting a clean bill of health we have not earned.
            if not opts['dry_run']:
                RowCountSnapshot.objects.create(
                    counts=counts, table_count=len(counts), total_rows=total,
                    note='baseline — first run, nothing to compare against')
            self.stdout.write(self.style.WARNING(
                f'baseline recorded: {len(counts)} tables, {total:,} rows. '
                'No comparison possible on a first run.'))
            return

        drops = find_drops(previous.counts or {}, counts)
        delta = total - (previous.total_rows or 0)
        self.stdout.write(
            f'{len(counts)} tables, {total:,} rows '
            f'({delta:+,} since {previous.taken_at:%Y-%m-%d %H:%M}) — '
            f'{len(drops)} table(s) lost rows')

        for d in drops:
            tag = 'PROTECTED' if d['protected'] else 'drop'
            self.stdout.write(self.style.ERROR(
                f'  {tag}: {d["table"]} {d["before"]:,} -> {d["after"]:,} '
                f'(-{d["lost"]:,}, {d["pct"]}%)'))

        if opts['dry_run']:
            self.stdout.write('--dry-run: nothing written, nothing sent')
            return

        snap = RowCountSnapshot.objects.create(
            counts=counts, table_count=len(counts), total_rows=total,
            note=(f'{len(drops)} table(s) lost rows' if drops else 'no losses'))

        if not drops:
            return

        # Something is gone. Tell him, and never let a mail failure hide the loss.
        try:
            from core.notifications import send_html_with_cfo_cc
            html = build_alert_html(drops, previous.taken_at, snap.taken_at)
            recipient = opts['to'] or 'pganesharajah@alphadirect.co.bw'
            sent = send_html_with_cfo_cc(
                subject=f'Omni: {sum(d["lost"] for d in drops):,} row(s) disappeared '
                        f'from {len(drops)} table(s)',
                html=html, to=[recipient],
                text_fallback='; '.join(
                    f'{d["table"]} {d["before"]}->{d["after"]} (-{d["lost"]})' for d in drops),
            )
            snap.alerted = bool(sent)
            snap.save(update_fields=['alerted'])
            self.stdout.write(self.style.WARNING(f'alert emailed (send result {sent})'))
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(self.style.ERROR(
                f'COULD NOT EMAIL THE ALERT: {exc!r} — the loss above is still real'))

        raise SystemExit(1)
