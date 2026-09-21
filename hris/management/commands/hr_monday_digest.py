from django.core.management.base import BaseCommand
from django.utils import timezone

from core.notifications import send_html_with_cfo_cc
from hris.contract_followup import _digest_counts, digest_html
from hris.hr_settings import get_setting


class Command(BaseCommand):
    help = "Send Monday HR digest to HR recipients. Dry-run by default; use --commit to send."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            dest="commit",
            default=False,
            help="Actually send the digest email.",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        subject, html = digest_html(today)
        counts = _digest_counts(today)
        recipients = get_setting("contract_reminder_recipients", ["dikgopoleng@alphadirect.co.bw", "ubutale@alphadirect.co.bw", "hc@alphadirect.co.bw"])

        self.stdout.write(f"Subject: {subject}")
        self.stdout.write(f"Recipients: {', '.join(recipients)}")
        self.stdout.write(f"Counts: {counts}")

        if options["commit"]:
            send_html_with_cfo_cc(subject, html, recipients, cc_cfo=True)
            self.stdout.write(self.style.SUCCESS("Sent Monday HR digest."))
        else:
            self.stdout.write("Dry run; use --commit to send.")
