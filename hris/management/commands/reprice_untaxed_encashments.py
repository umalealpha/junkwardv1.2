"""reprice_untaxed_encashments — put the PAYE figure on leave encashments that
were raised BEFORE PAYE withholding existed (CFO directive 2026-08-17).

Migration `hris/0068` backfills every pre-existing row to net = gross, tax = 0,
which is the literal truth: no PAYE was withheld on them. That is right for rows
already PAID or REJECTED — history must not be rewritten.

But rows still IN FLIGHT (pending CFO / HR / Finance, or approved-but-unpaid)
have not been paid yet, so they must be taxed before they are. This command does
exactly that, and nothing else:

  * touches ONLY rows with tax_amount = 0 that are not paid and not rejected;
  * leaves days, daily_rate and the GROSS amount untouched — the approvals
    already given were for those, and they do not change;
  * recomputes the PAYE base from the employee's latest payslip and the ACTIVE
    tax brackets, exactly as a new application would.

Run it AFTER `seed_burs_2026_2027`, or it will use the old 25 % top rate.

    python manage.py reprice_untaxed_encashments --dry-run     # look first
    python manage.py reprice_untaxed_encashments
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from hris.leave_encash_models import LeaveEncashment
from hris.leave_encash_service import tax_and_net, tax_base_for


class Command(BaseCommand):
    help = ('Compute PAYE + net on in-flight leave encashments raised before '
            'PAYE withholding existed. Never touches paid or rejected rows.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        dry = opts['dry_run']

        # In flight = awaiting a signature, or approved but not yet paid.
        candidates = (LeaveEncashment.objects
                      .filter(status__in=[LeaveEncashment.Status.PENDING_CFO,
                                          LeaveEncashment.Status.PENDING_HR,
                                          LeaveEncashment.Status.PENDING_FINANCE,
                                          LeaveEncashment.Status.APPROVED],
                              payroll_processed=False,
                              tax_amount=Decimal('0.00'))
                      .select_related('employee')
                      .order_by('created_at'))

        if not candidates:
            self.stdout.write(self.style.SUCCESS(
                'Nothing to reprice — no untaxed in-flight encashments.'))
            return

        self.stdout.write(f'{candidates.count()} in-flight encashment(s) with no '
                          f'PAYE figure:\n')
        total_tax = Decimal('0.00')
        skipped = 0

        with transaction.atomic():
            for enc in candidates.select_for_update():
                base, src = tax_base_for(enc.employee)
                if base <= 0:
                    # No payslip to tax against — leave it alone rather than
                    # guess. It shows net = gross until payroll is loaded.
                    self.stdout.write(self.style.WARNING(
                        f'  ! {enc.employee.full_name}: no payslip taxable pay '
                        f'— SKIPPED, left at net = gross.'))
                    skipped += 1
                    continue

                tax, net = tax_and_net(base, enc.amount)
                self.stdout.write(
                    f'  ~ {enc.employee.full_name:32s} {enc.status:16s} '
                    f'gross {enc.amount:>11,.2f}  tax {tax:>10,.2f}  '
                    f'net {net:>11,.2f}   (base {base:,.2f} from {src or "?"})')
                total_tax += tax
                if not dry:
                    enc.tax_base = base
                    enc.tax_amount = tax
                    enc.net_amount = net
                    enc.save(update_fields=['tax_base', 'tax_amount',
                                            'net_amount', 'updated_at'])

            if dry:
                transaction.set_rollback(True)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"DRY RUN  " if dry else ""}Repriced '
            f'{candidates.count() - skipped} row(s); total PAYE now withheld '
            f'BWP {total_tax:,.2f}.'
            + (f' {skipped} skipped (no payslip).' if skipped else '')))
        if dry:
            self.stdout.write(self.style.WARNING('No DB changes (--dry-run).'))
