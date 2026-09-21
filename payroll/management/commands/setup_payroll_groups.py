"""
Idempotently create the `payroll_poster` Group used by
payroll.services._can_post_payroll.
"""

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


GROUPS = [
    ('payroll_poster',
     'May post payroll periods to the GL via payroll.services.post_payroll_period.'),
]


class Command(BaseCommand):
    help = 'Create the payroll-related Django groups if they do not already exist.'

    def handle(self, *args, **options):
        created = []
        existed = []
        for name, _description in GROUPS:
            group, was_created = Group.objects.get_or_create(name=name)
            (created if was_created else existed).append(group.name)

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created groups: {', '.join(created)}"
            ))
        if existed:
            self.stdout.write(
                f"Already existed: {', '.join(existed)}"
            )
