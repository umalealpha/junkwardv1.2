"""Email every Alpha Nexus tester their competition standings.

Run on demand or from cron:
    python manage.py nexus_standings_email
"""
from django.core.management.base import BaseCommand

from rewards.nexus_standings import send_standings_to_all


class Command(BaseCommand):
    help = 'Email all Alpha Nexus testers their personalised standings + prize push.'

    def handle(self, *args, **options):
        result = send_standings_to_all()
        self.stdout.write(self.style.SUCCESS(
            f"Standings emailed: {result['sent']} of {result['total']} testers."))
