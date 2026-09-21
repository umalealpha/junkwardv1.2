"""
regulatory/management/commands/setup_capital_parameters.py

Idempotent — seeds the eight parameters that drive the capital-adequacy
calculation. ALL VALUES ARE PLACEHOLDERS — the CFO MUST verify against the
current NBFIRA Insurance Industry Regulations before any real submission.

Run:
    python manage.py setup_capital_parameters
"""

from django.core.management.base import BaseCommand

from regulatory.models import CapitalRequirementParameter


PARAMS = [
    # (code, label, value, notes)
    ('minimum_capital_floor', 'Minimum Capital Floor (BWP)',
     '5000000',
     'Absolute minimum capital. VERIFY against current NBFIRA general-insurer floor.'),
    ('opex_factor', 'Opex factor (decimal, reg 4(b) — 0.25 = 25%)',
     '0.25',
     'Insurance Industry Regulations 2019 (S.I. 68 of 2019) reg 4: the capital '
     'target is the higher of P5,000,000 or 25% of the operating expenses '
     'estimated for the following year. VERIFY against the current regulations '
     'and set an effective date to adopt it.'),
    ('estimated_annual_opex', 'Estimated opex for the following year (BWP, blank = use last completed FY)',
     '',
     'Optional override. Blank means the last completed financial year\'s actual '
     'operating expenses from the MA P&L are used as the estimate. Set this once '
     'a budget for the following year exists.'),
    # premium_factor and claims_factor were REMOVED on 2026-08-15. Neither
    # appears anywhere in Botswana insurance law; together they produced the
    # withdrawn 612% ratio. Existing rows are left in the database for the audit
    # trail but no longer feed the requirement.
    ('compliant_threshold', 'CAR threshold for COMPLIANT status',
     '1.25',
     'Capital Adequacy Ratio (Available / Required) at or above this is COMPLIANT.'),
    ('margin_threshold', 'CAR threshold for MARGIN status',
     '1.00',
     'CAR at or above this but below the compliant threshold is MARGIN (watch list).'),
    ('gwp_account_codes', 'GWP account codes (CSV)',
     '4100',
     'Comma-separated list of revenue accounts that count as Gross Written Premium.'),
    ('claims_account_codes', 'Claims account codes (CSV)',
     '5100,5110',
     'Comma-separated list of expense accounts that count as claims incurred.'),
    ('intangibles_account_codes', 'Intangibles deduction codes (CSV)',
     '',
     'Optional: comma-separated list of asset-side accounts whose balance is '
     'deducted from equity for the purposes of available capital.'),
    ('technical_provision_account_codes', 'Technical provision codes (CSV) — disclosure only',
     '205001,205002,208002,208004,212003,212004,212010,2120015,2200,2210,2220,2300,2310',
     'All technical-provision (reserve) accounts. Those sitting in a NET DEBIT '
     'are the reinsurers\' share; they are DISCLOSED on the capital-adequacy '
     'screen but EXCLUDED from own funds — Schedule 1 items 8.1-8.11 do not list '
     'reinsurance recoverable as an allowed asset (CFO 2026-08-15, overriding '
     'the 2026-07-02 add-back directive).'),
]


class Command(BaseCommand):
    help = 'Seed regulatory-capital parameters with placeholder values.'

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for code, label, value, notes in PARAMS:
            obj, was_created = CapitalRequirementParameter.objects.get_or_create(
                code=code,
                defaults={
                    'label': label,
                    'value': value,
                    'notes': notes,
                    'is_active': True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f'  + {code} = {value}'))
            else:
                skipped += 1

        self.stdout.write(self.style.WARNING(
            '\n!! These are PLACEHOLDER values. The CFO must verify them against '
            'the current NBFIRA Insurance Industry Regulations before any '
            'real capital-adequacy submission. Edit via the admin or '
            '/api/v1/capital-parameters/.'
        ))
        self.stdout.write(self.style.SUCCESS(f'\nDone. {created} created, {skipped} already present.'))
