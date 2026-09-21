"""
Management command: close_period

  python manage.py close_period 2026-04                # run checks + close
  python manage.py close_period 2026-04 --dry-run      # checks only
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from ledger.models import FiscalPeriod
from ledger.period_close import close_period, dry_run_close_checks


class Command(BaseCommand):
    help = 'Run pre-close checks and (optionally) close a fiscal period.'

    def add_arguments(self, parser):
        parser.add_argument('period_name', help='e.g. 2026-04')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show issues but do not close.')
        parser.add_argument('--user', help='Username to attribute the close to. '
                                           'Defaults to the first superuser.')

    def handle(self, *args, **options):
        try:
            period = FiscalPeriod.objects.get(period_name=options['period_name'])
        except FiscalPeriod.DoesNotExist:
            raise CommandError(f"Period {options['period_name']} not found.")

        issues = dry_run_close_checks(period)

        if options['dry_run']:
            if not issues:
                self.stdout.write(self.style.SUCCESS(
                    f"\n{period.period_name} is READY TO CLOSE — no outstanding items.\n"
                ))
                return
            self.stdout.write(self.style.WARNING(
                f"\n{period.period_name} has outstanding items:"
            ))
            for check, items in issues.items():
                self.stdout.write(f"\n  {check}:")
                for item in items:
                    self.stdout.write(f"    - {item}")
            return

        # Real close
        username = options.get('user')
        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User {username} not found.")
        else:
            user = User.objects.filter(is_superuser=True).order_by('pk').first()
            if user is None:
                raise CommandError("No superuser found and no --user supplied.")

        try:
            close_period(period, user)
        except Exception as e:
            raise CommandError(str(e))

        self.stdout.write(self.style.SUCCESS(
            f"\n{period.period_name} CLOSED by {user.username}.\n"
        ))
