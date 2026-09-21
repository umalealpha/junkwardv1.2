"""Build or refresh supplier reconciliation runs.

Designed to be scheduled daily so the board is always current:

    python manage.py build_supplier_recon                  # current month, all entities
    python manage.py build_supplier_recon --period 2026-06  # a specific month
    python manage.py build_supplier_recon --company ADIC
    python manage.py build_supplier_recon --dry-run

Finalised months are skipped, never rebuilt — a signed-off reconciliation is
only reopened deliberately, through the app, with a reason on record.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Company

from ...constants import RunStatus
from ...models import SupplierReconRun
from ...services import build_recon_run


class Command(BaseCommand):
    help = 'Build or refresh the monthly supplier payables reconciliation.'

    def add_arguments(self, parser):
        parser.add_argument('--period', default=None,
                            help="Month to build, YYYY-MM. Defaults to the "
                                 "current month.")
        parser.add_argument('--company', default=None,
                            help='Company code (e.g. ADIC). Defaults to every '
                                 'active entity.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be built, change nothing.')

    def handle(self, *args, **options):
        period = options['period'] or timezone.localdate().strftime('%Y-%m')
        code = options['company']
        dry = options['dry_run']

        companies = Company.objects.filter(is_active=True).order_by('code')
        if code:
            companies = companies.filter(code__iexact=code)
            if not companies.exists():
                raise CommandError(f"No active company with code '{code}'.")

        built = skipped = failed = 0
        for company in companies:
            existing = SupplierReconRun.objects.filter(
                company=company, period_label=period).first()
            if existing and existing.status == RunStatus.FINALISED:
                self.stdout.write(
                    f"  {company.code} {period}: SKIP — finalised "
                    f"{existing.finalised_at:%Y-%m-%d}")
                skipped += 1
                continue

            if dry:
                verb = 'refresh' if existing else 'create'
                self.stdout.write(f"  {company.code} {period}: would {verb}")
                continue

            try:
                run = build_recon_run(company, period)
            except ValidationError as exc:
                self.stderr.write(self.style.ERROR(
                    f"  {company.code} {period}: FAILED — "
                    f"{'; '.join(exc.messages)}"))
                failed += 1
                continue

            built += 1
            self.stdout.write(
                f"  {company.code} {period}: {run.lines.count()} suppliers, "
                f"invoiced {run.total_invoiced}, unpaid {run.total_unpaid}, "
                f"held {run.total_held}")

        if dry:
            self.stdout.write(self.style.WARNING('Dry run — nothing written.'))
            return

        summary = f"Built {built}, skipped {skipped}"
        if failed:
            self.stderr.write(self.style.ERROR(f"{summary}, FAILED {failed}"))
            raise CommandError(f"{failed} run(s) failed.")
        self.stdout.write(self.style.SUCCESS(summary))
