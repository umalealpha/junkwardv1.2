"""
assets/management/commands/scan_asset_signoffs.py

Daily cron — scans the asset register and the calendar to (re-)materialise
the AssetSignOff queue. Idempotent: each (kind, asset, period_label) tuple
gets at most one open record at a time.

What it does:
  1. For every active Asset whose NBV is at or below salvage AND status
     is ACTIVE, ensure there's an open FULLY_DEPRECIATED_REVIEW signoff.
     Auto-resolves once the asset is disposed or the signoff is completed.
  2. Two SEMI_ANNUAL_COUNT signoffs per fiscal year: H1 (due 31-Dec of the
     calendar year — Botswana fiscal year is July-June) and H2 (due 30-Jun).
  3. Marks any non-completed signoff whose due_date has passed as OVERDUE.

Run:
    python manage.py scan_asset_signoffs
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from assets.models import Asset, AssetSignOff


ZERO = Decimal('0.00')


def _half_year_dates(today: date) -> list[tuple[str, date]]:
    """Return [(label, due_date), ...] for the two checkpoints in this fiscal year."""
    # Botswana fiscal year is July–June. H1 ends 31-Dec, H2 ends 30-Jun.
    year = today.year
    out = []
    h1_due = date(year, 12, 31)
    if h1_due >= today:
        out.append((f'H1-{year}', h1_due))
    h2_due = date(year + 1 if today.month > 6 else year, 6, 30)
    if h2_due >= today:
        out.append((f'H2-{h2_due.year}', h2_due))
    return out


class Command(BaseCommand):
    help = 'Materialise asset sign-off records (fully-depreciated reviews + half-year counts).'

    def handle(self, *args, **options):
        today = timezone.localdate()

        # 1. Fully-depreciated assets still in use ----------------------------
        fd_created = 0
        fd_resolved = 0
        active_assets = Asset.objects.filter(status=Asset.Status.ACTIVE)
        for asset in active_assets:
            try:
                fully_depr = asset.is_fully_depreciated
            except Exception:  # noqa: BLE001
                continue

            existing_open = AssetSignOff.objects.filter(
                kind=AssetSignOff.Kind.FULLY_DEPRECIATED_REVIEW,
                asset=asset,
                status__in=(AssetSignOff.Status.PENDING,
                            AssetSignOff.Status.PARTIALLY_SIGNED,
                            AssetSignOff.Status.OVERDUE),
            ).first()

            if fully_depr and existing_open is None:
                AssetSignOff.objects.create(
                    kind=AssetSignOff.Kind.FULLY_DEPRECIATED_REVIEW,
                    asset=asset,
                    period_label=asset.tag_number,
                    due_date=today + timedelta(days=30),
                    company=asset.company,
                )
                fd_created += 1
            elif (not fully_depr) and existing_open is not None and existing_open.status != AssetSignOff.Status.COMPLETED:
                # Asset is no longer fully depreciated (revaluation, NBV restored, etc.)
                # — close out the open review.
                existing_open.status = AssetSignOff.Status.CANCELLED
                existing_open.notes = (
                    existing_open.notes + '\nAuto-cancelled: asset NBV restored above salvage.'
                ).strip()
                existing_open.save()
                fd_resolved += 1

        # 2. Semi-annual asset counts ----------------------------------------
        sa_created = 0
        for label, due in _half_year_dates(today):
            existing = AssetSignOff.objects.filter(
                kind=AssetSignOff.Kind.SEMI_ANNUAL_COUNT,
                period_label=label,
            ).first()
            if existing is None:
                AssetSignOff.objects.create(
                    kind=AssetSignOff.Kind.SEMI_ANNUAL_COUNT,
                    period_label=label,
                    due_date=due,
                    second_role_label='CFO',
                )
                sa_created += 1

        # 3. Mark overdue ----------------------------------------------------
        marked_overdue = (
            AssetSignOff.objects
            .filter(due_date__lt=today)
            .exclude(status__in=(AssetSignOff.Status.COMPLETED, AssetSignOff.Status.CANCELLED))
            .exclude(status=AssetSignOff.Status.OVERDUE)
            .update(status=AssetSignOff.Status.OVERDUE)
        )

        self.stdout.write(self.style.SUCCESS(
            f'Asset sign-off scan {today.isoformat()}: '
            f'FD reviews +{fd_created}/-{fd_resolved} '
            f'half-year counts +{sa_created} '
            f'overdue marked {marked_overdue}'
        ))
