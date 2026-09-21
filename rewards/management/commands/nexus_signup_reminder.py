"""Email a reminder (with app-install links) to testers who haven't signed up.
Wired to a Monday 08:00 CAT cron."""
from django.core.management.base import BaseCommand
from rewards.nexus_signup import send_signup_reminders


class Command(BaseCommand):
    help = 'Email install/sign-in reminders to Alpha Nexus testers who have not signed up.'

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="Run even on a public holiday (bypass the holiday pause).")

    def handle(self, *args, **options):
        # Public-holiday quiet time (CFO 2026-07-19): skip on a Botswana
        # non-working public holiday; the Monday cron simply resumes next week.
        from hris.workforce_brief import is_holiday_off
        if is_holiday_off() and not options.get("force"):
            self.stdout.write(self.style.WARNING(
                "Public holiday (Botswana) — Nexus signup reminder paused; "
                "resumes next working day."))
            return
        r = send_signup_reminders()
        self.stdout.write(self.style.SUCCESS(
            f"Reminders sent: {r['reminded']} (still pending of {r['total']})."))
