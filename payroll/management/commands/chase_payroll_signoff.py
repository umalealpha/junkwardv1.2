"""
chase_payroll_signoff — keep the DUAL payroll sign-off moving.

CFO directive 2026-07-28: a month closes when an HR signer (Unami/Dorothy) AND
a Finance signer (Kago/Pako) have both signed. The CFO is out of the routine
loop. This cron puts each outstanding side on the right people's dashboards +
their daily reminder emails, and clears tasks once a month is fully signed.

Idempotent: safe to run daily from cron — one open task per person per
company-month-side, and closed months have their tasks cleared.

    python manage.py chase_payroll_signoff              # latest period
    python manage.py chase_payroll_signoff --period 2026-07
    python manage.py chase_payroll_signoff --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from payroll.models import PayrollPeriod, PayrollSignOff
from payroll.signoff_service import companies_awaiting_signoff, live_payroll_totals


class Command(BaseCommand):
    help = 'Chase dual payroll sign-off: remind HR and Finance signers.'

    def add_arguments(self, parser):
        parser.add_argument('--period', default='',
                            help='Period name, e.g. 2026-07 (default: latest).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be raised, change nothing.')

    def handle(self, *args, **opts):
        from core.notifications import (
            close_payroll_side_tasks, notify_payroll_side_due,
        )

        name = (opts['period'] or '').strip()
        if name:
            period = PayrollPeriod.objects.filter(period_name=name).first()
            if period is None:
                raise CommandError(f'No payroll period named {name}.')
        else:
            period = PayrollPeriod.objects.order_by('-start_date').first()
            if period is None:
                self.stdout.write('No payroll periods exist — nothing to chase.')
                return

        dry = opts['dry_run']
        self.stdout.write(f'Period {period.period_name}'
                          + (' (dry run)' if dry else ''))

        # ── Outstanding sides -> remind the right people ─────────────────────
        hr_chased = fin_chased = 0
        for company, row, needs_hr, needs_fin in companies_awaiting_signoff(period):
            totals = live_payroll_totals(period, company)
            note = ''
            if row is not None and row.status == PayrollSignOff.Status.REJECTED:
                note = f' [sent back: {row.rejection_reason}]'
            elif row is not None and row.drift() is not None:
                note = ' [figures changed — both must sign again]'
            self.stdout.write(
                f'  {company.name} — {totals["headcount"]} people, '
                f'BWP {totals["net"]:,.2f} net; '
                f'needs {"HR " if needs_hr else ""}{"Finance" if needs_fin else ""}'.rstrip()
                + note)
            if not dry:
                if needs_hr:
                    hr_chased += notify_payroll_side_due(period, company, 'hr')
                if needs_fin:
                    fin_chased += notify_payroll_side_due(period, company, 'finance')

        # ── Fully signed & clean -> clear both sides' tasks ──────────────────
        cleared = 0
        for row in (PayrollSignOff.objects
                    .filter(period=period, status=PayrollSignOff.Status.APPROVED)
                    .select_related('company', 'period')):
            if row.drift() is not None or not row.both_signed:
                continue
            cleared += 1
            if not dry:
                close_payroll_side_tasks(row.company, row.period, 'hr')
                close_payroll_side_tasks(row.company, row.period, 'finance')

        self.stdout.write(self.style.SUCCESS(
            f'{period.period_name}: {hr_chased} HR + {fin_chased} Finance '
            f'reminder(s) raised, {cleared} fully signed off.'))
