"""python manage.py send_welcome_back_digest [--force]

Send ONE 'welcome back' catch-up email per person on the FIRST WORKING DAY after a
public-holiday break — the tasks that piled up, in a single friendly message
instead of a burst of separate reminders (CFO directive 2026-07-19).

Self-gating: does nothing on an ordinary day, so it is safe to run from a plain
daily early-morning cron — it only fires the morning staff come back.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Welcome-back task digest on the first working day after a public holiday."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Send now even if today is not the first working day after a holiday.")

    def handle(self, *args, **opts):
        # Suppressed (CFO 2026-07-22): the piled-up tasks this used to email
        # separately are already in the ONE Daily Brief's "Pending tasks"
        # section, so this standalone "welcome back" email just added a third
        # overlapping message. Keep the command as a no-op so its cron entry is
        # harmless; the Daily Brief covers the catch-up.
        self.stdout.write("welcome-back digest folded into the Daily Brief — nothing sent.")
        return
