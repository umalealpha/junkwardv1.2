"""python manage.py send_leave_digest [--force]

Daily leave digest for the HR head (Unami Butale, 2026-07-27): who is on leave
today, and who is going on leave in the next 2 weeks. One branded email each
working morning — HR only (the CFO is cc'd by the house rule).

Self-gating: skips weekends and non-working public holidays, and sends nothing
on a day with no leave to report, so it is safe on a plain daily cron.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris.amendment_service import UNAMI_EMAIL
from hris.leave_digest import build_leave_digest
from hris.workforce_brief import is_holiday_off


class Command(BaseCommand):
    help = "Daily HR leave digest — on leave today + going on leave in the next 2 weeks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Send even on a weekend / public holiday.")
        parser.add_argument(
            "--to", default=UNAMI_EMAIL,
            help="Override the recipient (default: the HR head).")

    def handle(self, *args, **opts):
        today = timezone.localdate()
        if not opts["force"] and is_holiday_off(today):
            self.stdout.write("Weekend / public holiday — leave digest paused.")
            return

        digest = build_leave_digest(today)
        if digest is None:
            self.stdout.write("No leave to report today — nothing sent.")
            return

        from core.notifications import send_html_with_cfo_cc
        sent = send_html_with_cfo_cc(
            digest["subject"], digest["html"], [opts["to"]],
            text_fallback="Your daily leave digest is in the HTML version of this email.",
        )
        self.stdout.write(self.style.SUCCESS(
            f"Leave digest sent to {opts['to']} (result={sent})."))
