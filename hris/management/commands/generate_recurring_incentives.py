"""
generate_recurring_incentives — materialise the month's incentive requests
from the active RecurringIncentive templates (CFO directive 2026-07-22).

Idempotent: a (template, period) pair that already produced a request is
skipped, so this is safe to run monthly by cron or by hand.

  python manage.py generate_recurring_incentives
  python manage.py generate_recurring_incentives --period 2026-08
  python manage.py generate_recurring_incentives --user-email pganesharajah@alphadirect.co.bw
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from hris.amendment_service import CFO_EMAIL
from hris.incentive_service import current_period, generate_recurring_for_period


def _resolve_user(email: str | None) -> User:
    """The actor the generated requests are attributed to. Must be a manager /
    admin / superuser (generate_recurring_for_period re-checks this)."""
    if email:
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No user found with email {email!r}.")
        return user
    user = User.objects.filter(email__iexact=CFO_EMAIL).first()
    if user is not None:
        return user
    user = User.objects.filter(is_superuser=True).order_by('id').first()
    if user is None:
        raise CommandError(
            "No --user-email given and no CFO / superuser found to run as.")
    return user


class Command(BaseCommand):
    help = ("Generate the active recurring incentives into incentive requests "
            "for a period (idempotent).")

    def add_arguments(self, parser):
        parser.add_argument(
            '--period', default=None,
            help='Target month YYYY-MM (defaults to the current month).')
        parser.add_argument(
            '--user-email', default=None,
            help='Attribute the generated requests to this user (defaults to '
                 'the CFO, else the first superuser).')

    def handle(self, *args, **options):
        period = options.get('period') or current_period()
        user = _resolve_user(options.get('user_email'))

        try:
            result = generate_recurring_for_period(period, user)
        except Exception as exc:                       # noqa: BLE001
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"Recurring incentives for {result['period']}: "
            f"{result['created_count']} created, "
            f"{result['skipped_count']} already existed (skipped)."))
        for req in result['created']:
            self.stdout.write(f"  + {req.title} — BWP {req.total}")
