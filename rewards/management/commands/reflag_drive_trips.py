"""Re-stamp every drive trip with the current data-quality verdict.

Run this after ANY change to the drive_score quality constants (MIN_TRIP_KM,
MIN_TRIP_MIN, PEAK_SUSTAIN_SEC, ABSURD_KMH) — otherwise the stored flags that
nexus_standings / nexus_growth filter on disagree with what driving_profile
derives, and the same screen can show two different answers.

Read paths are unaffected by a stale flag for the member's own profile (that
derives), so this is a records-consistency job, not a user-facing one.
"""
from django.core.management.base import BaseCommand

from rewards import drive_score
from rewards.models import CustomerDriveTrip


class Command(BaseCommand):
    help = 'Re-apply the drive-trip data-quality verdict to every stored trip.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        total = changed = 0
        for trip in CustomerDriveTrip.objects.all().iterator():
            total += 1
            q = drive_score.trip_quality(distance_km=trip.distance_km,
                                         duration_min=trip.duration_min,
                                         max_speed=trip.max_speed)
            stale = (trip.is_valid, trip.invalid_reason, trip.speed_reliable) != (
                q['valid'], q['reason'], q['speed_reliable'])
            if not stale:
                continue
            changed += 1
            self.stdout.write(
                f"  {trip.started_at:%Y-%m-%d %H:%M}  {trip.distance_km:.2f} km / "
                f"{trip.duration_min:.1f} min / {trip.max_speed:.0f} km/h  ->  "
                f"valid={q['valid']} reason={q['reason'] or '-'} speed_reliable={q['speed_reliable']}")
            if not dry:
                drive_score.apply_quality(trip)
        verb = 'would change' if dry else 'updated'
        self.stdout.write(self.style.SUCCESS(f'{total} trip(s) checked, {changed} {verb}.'))
