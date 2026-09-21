"""
Daily: does the money RealPay says it collected reach Omni's payment ledger?

WHY (found 8-Sep-2026): roughly 28,100 successful RealPay collections from June to
August 2026 never reached `payment_transactions` — matched contract by contract,
18.6% of June, 86.7% of July and 46.5% of August — while RealPay itself kept
delivering normally. Nobody noticed for two months, because nothing was watching.
This command watches.

It does not mend the import (that sits with TheRiskCo). It ends the silence: the
moment the feed knows about collections the ledger does not, the CFO is told the same
morning instead of two months later.

Read-only. Every statement goes through integrations.graphite_ro, which refuses a
non-replica host and refuses anything that is not a SELECT. No data rows are printed
and no customer information is exposed by running it — months and counts only.

Usage
  python manage.py realpay_ledger_reconcile                 # 6 months, print only
  python manage.py realpay_ledger_reconcile --months 9
  python manage.py realpay_ledger_reconcile --email          # mail the CFO on a break
  python manage.py realpay_ledger_reconcile --email --always # mail even when clean
  python manage.py realpay_ledger_reconcile --asof 2026-08-31

SCHEDULING. infra/cron/realpay-reconcile.cron, listed in infra/install-crons.sh, at
04:00 UTC = 06:00 SAST — after the overnight paygate loads and before the finance day.
It runs through /usr/local/bin/omni-manage, not `docker compose exec`: the raw form was
retired on 2026-09-04 after a cron ran mid blue/green flip and the company lost a day's
attendance record.

EXIT CODES. Zero only when the check actually ran. An unreachable replica or a bad
--asof exits non-zero so cron records a failure rather than a clean night — a job that
fails quietly is worse than no job.
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations import realpay_ledger_reconcile as recon


class Command(BaseCommand):
    help = 'Reconcile RealPay collections against the Omni payment ledger (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('--months', type=int, default=6,
                            help='How many months back to compare (default 6).')
        parser.add_argument('--asof', default='',
                            help='Compare as at this date, YYYY-MM-DD (default today).')
        parser.add_argument('--email', action='store_true',
                            help='Email the CFO when a break is found.')
        parser.add_argument('--always', action='store_true',
                            help='With --email, send even when everything reconciles.')

    def handle(self, *args, **opts):
        asof = None
        if opts['asof']:
            try:
                asof = datetime.date.fromisoformat(opts['asof'][:10])
            except ValueError:
                # CommandError exits non-zero, so a mistyped cron entry is loud.
                raise CommandError('--asof must be YYYY-MM-DD')
        # Clamped like the view: --months 0 would return no rows and read "ok".
        months = max(2, min(24, int(opts['months'])))
        result = recon.compare(months=months, asof=asof)

        if not result.get('available'):
            # Tell somebody BEFORE exiting. There is no MAILTO in any cron file, so a
            # raise on its own writes to a log that nobody reads — which is precisely
            # the two-month silence this command exists to end.
            if opts['email']:
                self._email_outage(result)
            # Then exit NON-ZERO, so cron records a failure rather than a clean night.
            raise CommandError(
                f"could not run: {result.get('reason')}. This is an outage to report, "
                f"not a clean pass.")

        self.stdout.write(f"RealPay vs the payment ledger, as at {result['asof']}")
        self.stdout.write(f"{'month':9} {'RealPay':>9} {'ledger':>9} {'missing':>9} {'%':>7}   note")
        for r in result['rows']:
            note = ('still settling' if r['settling']
                    else 'BREAK' if r['breach']
                    else 'ok' if r['shortfall'] <= 0 else 'within tolerance')
            style = self.style.ERROR if r['breach'] else (lambda s: s)
            self.stdout.write(style(
                f"{r['month']:9} {r['feed']:>9,} {r['ledger']:>9,} "
                f"{r['shortfall']:>9,} {r['shortfall_pct']:>6.1f}%   {note}"))

        self._save(result)

        line = recon.summary_line(result)
        if result['verdict'] == 'break':
            self.stdout.write(self.style.ERROR(f"\n{line}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n{line}"))

        if opts['email'] and (result['verdict'] == 'break' or opts['always']):
            self._email(result, line)

    def _save(self, result):
        """Store the answer so the screen can read it instead of recomputing.

        Append-only: a correction is a new row, never an overwrite, so what we believed
        on each morning survives. A failure to save must never stop the alarm going
        out, so it is logged and swallowed.
        """
        from integrations.models import RealpayReconSnapshot

        try:
            RealpayReconSnapshot.objects.create(
                asof=datetime.date.fromisoformat(result['asof']),
                window_months=result.get('window_months') or 6,
                verdict=result.get('verdict') or 'ok',
                summary=recon.summary_line(result)[:2000],
                total_shortfall=sum(r['shortfall'] for r in result.get('breaches', [])),
                months_breached=len(result.get('breaches', [])),
                payload=result,
            )
            self.stdout.write(self.style.SUCCESS('saved for the screen'))
        except Exception as exc:      # noqa: BLE001 — never block the alarm
            self.stderr.write(self.style.WARNING(
                f'could not save the snapshot ({exc.__class__.__name__}); '
                f'the screen will fall back to computing live'))

    def _email_outage(self, result):
        """The check could not run. Say so, to a person, the same morning.

        An unreachable replica and a silent RealPay feed both land here. Either way
        the honest message is "we do not know", never "all clear".
        """
        from core.notifications import send_html_with_cfo_cc

        reason = result.get('reason') or 'unknown'
        html = (
            f"<p>The daily check of RealPay collections against Omni's payment ledger "
            f"<b>could not run</b> this morning.</p>"
            f"<p>Reason: {reason}</p>"
            f"<p>This is an outage, not a clean bill. Until it runs, nobody is watching "
            f"whether the collections RealPay reports are reaching our records \u2014 which "
            f"is how roughly 28,100 collections went missing between June and August "
            f"without anyone noticing.</p>"
            f"<p>If the reason mentions the RealPay feed having no rows, the feed itself "
            f"has stopped arriving and that is the more urgent of the two.</p>")
        try:
            sent = send_html_with_cfo_cc(
                subject='RealPay reconciliation could not run',
                html=html, to=[settings.REALPAY_RECONCILE_EMAIL],
                text_fallback=f'RealPay reconciliation could not run: {reason}')
            self.stdout.write(self.style.WARNING(f'outage emailed (sent={sent})'))
        except Exception as exc:      # noqa: BLE001 — never mask the outage itself
            self.stderr.write(self.style.ERROR(
                f'outage email failed ({exc.__class__.__name__}) — the outage still stands'))

    def _email(self, result, line):
        """House template, house cc rules, house no-reply banner.

        Deliberately NOT a raw EmailMessage: send_html_with_cfo_cc applies the HTML
        house template, the excoboard@ cc rule and the internal no-reply banner, and
        enforces the never-cc list. A hand-rolled plain-text send bypasses all of it
        (standing order: all email HTML, house template).
        """
        from core.notifications import send_html_with_cfo_cc

        to = [settings.REALPAY_RECONCILE_EMAIL]
        breaking = result['verdict'] == 'break'

        def row_html(r):
            state = ('still settling' if r['settling']
                     else 'not reaching us' if r['breach'] else 'reconciles')
            colour = '#C62828' if r['breach'] else '#5b6472'
            return (
                f"<tr>"
                f"<td style='padding:4px 10px;'>{r['month']}</td>"
                f"<td style='padding:4px 10px;text-align:right;'>{r['feed']:,}</td>"
                f"<td style='padding:4px 10px;text-align:right;'>{r['ledger']:,}</td>"
                f"<td style='padding:4px 10px;text-align:right;color:{colour};'>"
                f"{r['shortfall']:,} ({r['shortfall_pct']:.1f}%)</td>"
                f"<td style='padding:4px 10px;text-align:right;'>{r['ledger_only']:,}</td>"
                f"<td style='padding:4px 10px;color:{colour};'>{state}</td></tr>")

        est = sum(r['estimated_value'] for r in result['breaches'])
        est_para = (
            f"<p>On the average value of the receipts that did land, the missing ones are "
            f"worth roughly <b>P{est:,.2f}</b>. That is an order of magnitude, not a figure "
            f"to book \u2014 the missing receipts may not be average.</p>" if est else '')

        html = (
            f"<p>{line}</p>"
            f"<table style='border-collapse:collapse;font-size:14px;'>"
            f"<tr style='background:#0D1B2A;color:#fff;'>"
            f"<th style='padding:6px 10px;text-align:left;'>Month</th>"
            f"<th style='padding:6px 10px;text-align:right;'>RealPay collected</th>"
            f"<th style='padding:6px 10px;text-align:right;'>Reached our records</th>"
            f"<th style='padding:6px 10px;text-align:right;'>Missing</th>"
            f"<th style='padding:6px 10px;text-align:right;'>Ledger only</th>"
            f"<th style='padding:6px 10px;text-align:left;'>State</th></tr>"
            + ''.join(row_html(r) for r in result['rows']) +
            f"</table>"
            f"{est_para}"
            f"<p>Matched contract by contract, not total against total. Receipts our ledger "
            f"holds that the RealPay feed does not are shown as \u201cledger only\u201d and are "
            f"never used to cancel out a shortfall \u2014 offsetting them was hiding about 16% "
            f"of any loss. The newest month is still filling and is never flagged.</p>"
            f"<p>This check reads only. It cannot mend the import \u2014 that sits with "
            f"TheRiskCo \u2014 but it will tell you the morning after the gap opens again.</p>")

        sent = send_html_with_cfo_cc(
            subject=('RealPay collections are not reaching the payment ledger'
                     if breaking else 'RealPay collections reconcile \u2014 nothing to do'),
            html=html, to=to, text_fallback=line)
        self.stdout.write(self.style.SUCCESS(f'emailed {to[0]} (sent={sent})')
                          if sent else self.style.ERROR('email failed'))
