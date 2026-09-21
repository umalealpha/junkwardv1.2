"""Email underwriters a reminder of issued quotations about to lapse, so a live
quote is followed up instead of quietly expiring. Runs daily from cron.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.html import escape

from underwriting.models import Quote


class Command(BaseCommand):
    help = "Remind underwriters of quotations lapsing within --days (default 5)."

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=5,
                            help='How many days ahead to look (default 5).')

    def handle(self, *args, **opts):
        from core.notifications import send_html_with_cfo_cc

        days = opts['days']
        today = timezone.localdate()
        cutoff = today + timedelta(days=days)
        qs = (Quote.objects
              .filter(status=Quote.Status.ISSUED, valid_until__isnull=False,
                      valid_until__gte=today, valid_until__lte=cutoff)
              .select_related('underwriter').order_by('valid_until'))

        by_uw: dict = {}
        for q in qs:
            by_uw.setdefault(q.underwriter_id, []).append(q)

        emails = 0
        for quotes in by_uw.values():
            uw = quotes[0].underwriter
            to = [uw.email.strip()] if (uw and (uw.email or '').strip()) else []
            if not to:
                continue
            rows = ''.join(
                f"<tr><td>{escape(q.quote_number)}</td>"
                f"<td>{escape(q.client_name)}</td>"
                f"<td style='text-align:right'>P{q.total:,.2f}</td>"
                f"<td>{q.valid_until:%d %b %Y}</td></tr>"
                for q in quotes)
            html = (
                f"<p>These quotation(s) lapse within {days} days — follow up before "
                f"they expire so the quote can convert.</p>"
                f"<table cellpadding='6' style='border-collapse:collapse' border='1'>"
                f"<tr style='background:#0D1B2A;color:#fff;text-align:left'>"
                f"<th>Quote</th><th>Client</th><th>Total</th><th>Valid until</th></tr>"
                f"{rows}</table>")
            # Operational nudge to the underwriter — not an EXCO matter, so don't
            # cc the CFO/board mailbox daily (CFO directive 2026-06-17).
            emails += send_html_with_cfo_cc(
                f"{len(quotes)} quotation(s) about to lapse", html, to, cc_cfo=False)

        self.stdout.write(self.style.SUCCESS(
            f"expiring-quote reminders: {len(by_uw)} underwriter(s), {emails} email(s) sent"))
