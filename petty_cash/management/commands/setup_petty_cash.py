"""
setup_petty_cash — idempotent seed for the default ADI petty cash location.

Run once after migrations:
    python manage.py setup_petty_cash

Creates a single PettyCashLocation called "Boardroom — BIH 2F" with a
P15,000 float, GL 1160 as the petty cash account and 1110 as the
reimbursing bank.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Company
from ledger.models import Account
from petty_cash.models import PettyCashLocation


DEFAULT_NAME = 'Boardroom — BIH 2F'
DEFAULT_COMPANY_CODE = 'ADIC'
DEFAULT_ADDRESS = (
    '2nd Floor, Bar Two, Botswana Innovation Hub, Plot 69184, Gaborone'
)


class Command(BaseCommand):
    help = 'Seed the default petty cash location (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--float',
            type=str,
            default='15000.00',
            help='Float amount in BWP (default 15000.00).',
        )
        parser.add_argument(
            '--name',
            type=str,
            default=DEFAULT_NAME,
            help=f'Location name (default "{DEFAULT_NAME}").',
        )
        parser.add_argument(
            '--company',
            type=str,
            default=DEFAULT_COMPANY_CODE,
            help=f'Company code that owns this float (default '
                 f'"{DEFAULT_COMPANY_CODE}"). Stamped on every petty-cash JE.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            petty = Account.objects.get(code='1160')
        except Account.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                "Account 1160 (Petty cash) not found. Run "
                "'setup_chart_of_accounts' first."
            ))
            return
        try:
            bank = Account.objects.get(code='1110')
        except Account.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                "Account 1110 (FNB BWP operating) not found. Run "
                "'setup_chart_of_accounts' first."
            ))
            return

        company = (Company.objects.filter(code__iexact=options['company']).first()
                   or Company.objects.filter(code=options['company']).first())
        if company is None:
            self.stderr.write(self.style.WARNING(
                f"Company '{options['company']}' not found — location will be "
                f"created without an entity. Petty-cash JEs will post with "
                f"company=NULL (no FY-lock, no per-company roll-up) until you "
                f"set it. Available codes: "
                f"{', '.join(Company.objects.values_list('code', flat=True)) or '(none)'}."
            ))

        loc, created = PettyCashLocation.objects.update_or_create(
            name=options['name'],
            defaults=dict(
                address=DEFAULT_ADDRESS,
                float_amount=Decimal(options['float']),
                company=company,
                petty_cash_account=petty,
                reimbursing_bank_account=bank,
                is_active=True,
            ),
        )
        verb = 'Created' if created else 'Refreshed'
        entity = company.code if company else 'NO ENTITY'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} petty cash location "{loc.name}" [{entity}] with float '
            f'P{loc.float_amount}.'
        ))
