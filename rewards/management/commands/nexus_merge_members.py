"""Merge one Alpha Nexus member record into another.

CFO 2026-09-08: he had signed up twice (work email + Gmail) and asked to
"merge them as one" so a single person is one line on the competition board.
Several other testers have the same problem, so this is a command, not a
one-off script.

What it does, inside one transaction:
  * moves every child record (points transactions, trips, scans, activities,
    step days, workouts, consents, feedback, sessions, tokens) to the KEEP
    member;
  * where a per-day uniqueness rule would collide (a health metric or a step
    day exists for the same date on both records) the KEEP row wins and the
    duplicate's row is LEFT BEHIND — never silently added together, because
    two step totals for one day is exactly how a double-count starts;
  * adds the duplicate's reward-point balance to the KEEP member;
  * marks the duplicate inactive and stamps its name, so nothing is deleted
    and the merge can be read back later.

Usage:
    manage.py nexus_merge_members --into <keep-email> --from <dup-email> [--apply]
Dry run by default: it prints exactly what would move and changes nothing.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

MERGED_SUFFIX = ' (merged)'


class Command(BaseCommand):
    help = 'Merge a duplicate Alpha Nexus member into the one you keep.'

    def add_arguments(self, parser):
        parser.add_argument('--into', required=True, help='email of the member to KEEP')
        parser.add_argument('--from', dest='dup', required=True, help='email of the DUPLICATE')
        parser.add_argument('--apply', action='store_true',
                            help='actually write the merge (default is a dry run)')

    def handle(self, *args, **opts):
        from rewards.models import RewardMember
        keep = self._one(RewardMember, opts['into'])
        dup = self._one(RewardMember, opts['dup'])
        if keep.pk == dup.pk:
            raise CommandError('--into and --from are the same member')

        moved, left = merge_member(keep, dup, apply=opts['apply'])
        for model_name, n in sorted(moved.items()):
            self.stdout.write(f'  {model_name}: {n} row(s) -> {keep.email}')
        for model_name, n in sorted(left.items()):
            self.stdout.write(self.style.WARNING(
                f'  {model_name}: {n} row(s) LEFT on the duplicate (same-day clash, kept row wins)'))
        self.stdout.write(f'  points: {dup.points_balance} added to {keep.points_balance}')
        if opts['apply']:
            self.stdout.write(self.style.SUCCESS(
                f'MERGED {dup.email or dup.customer_name} into {keep.email}'))
        else:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing written. Re-run with --apply.'))

    @staticmethod
    def _one(model, email):
        m = model.objects.filter(email__iexact=(email or '').strip()).first()
        if not m:
            raise CommandError(f'no Nexus member with email {email!r}')
        return m


def merge_member(keep, dup, apply: bool = False) -> tuple[dict, dict]:
    """Move dup's records onto keep. Returns (moved counts, left-behind counts)."""
    from rewards.models import (
        PointsTransaction, DrivingScore, HealthConsent, HealthMetric,
        CustomerDriveTrip, CustomerActivity, CustomerDeviceToken,
        CustomerStepDay, CustomerWorkoutSession, CustomerFeedback, CustomerSession,
    )
    # (model, per-day field) — None means no uniqueness rule to respect.
    plan = [
        (PointsTransaction, None), (DrivingScore, None), (HealthConsent, None),
        (CustomerDriveTrip, None), (CustomerActivity, None), (CustomerDeviceToken, None),
        (CustomerWorkoutSession, None), (CustomerFeedback, None), (CustomerSession, None),
        (HealthMetric, 'date'), (CustomerStepDay, 'day'),
    ]
    moved: dict[str, int] = {}
    left: dict[str, int] = {}

    with transaction.atomic():
        for model, day_field in plan:
            qs = model.objects.filter(member=dup)
            if day_field is None:
                n = qs.count()
                if n and apply:
                    qs.update(member=keep)
                if n:
                    moved[model.__name__] = n
                continue
            taken = set(model.objects.filter(member=keep).values_list(day_field, flat=True))
            movable = [r for r in qs if getattr(r, day_field) not in taken]
            clashing = qs.count() - len(movable)
            if movable and apply:
                (model.objects.filter(pk__in=[r.pk for r in movable])
                 .update(member=keep))
            if movable:
                moved[model.__name__] = len(movable)
            if clashing:
                left[model.__name__] = clashing

        if apply:
            keep.points_balance = (keep.points_balance or 0) + (dup.points_balance or 0)
            keep.save(update_fields=['points_balance', 'updated_at'])
            dup.points_balance = 0
            dup.is_active = False
            if not dup.customer_name.endswith(MERGED_SUFFIX):
                dup.customer_name = f'{dup.customer_name}{MERGED_SUFFIX}'
            dup.save(update_fields=['points_balance', 'is_active', 'customer_name', 'updated_at'])
    return moved, left
