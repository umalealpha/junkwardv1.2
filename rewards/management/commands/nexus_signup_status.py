"""List Alpha Nexus testers who have / haven't signed up."""
from django.core.management.base import BaseCommand
from rewards.nexus_signup import signup_status


class Command(BaseCommand):
    help = 'Show which invited Alpha Nexus testers have signed up (logged in) vs not.'

    def handle(self, *args, **options):
        st = signup_status()
        self.stdout.write(self.style.SUCCESS(f"Signed up: {st['signed_count']}/{st['total']}"))
        for e in st['signed_up']:
            self.stdout.write(f"  [x] {e}")
        self.stdout.write(self.style.WARNING(f"Not signed up: {st['pending_count']}/{st['total']}"))
        for e in st['not_signed_up']:
            self.stdout.write(f"  [ ] {e}")
