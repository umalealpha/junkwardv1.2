#!/usr/bin/env python
"""
seed_companies_v3.py — add 2 missing group entities (GCX, AIZ) and retag
existing ADIC FY25/FY26 JEs with company=ADIC so the dashboard's company
filter shows ADIC's numbers when ADIC is selected (currently those JEs
have company=NULL, which excludes them from filtered queries).

Run:
    python manage.py shell < ops/seeds/seed_companies_v3.py

Idempotent.
"""
from django.db import transaction
from core.models import Company
from ledger.models import JournalEntry

NEW_ENTITIES = [
    ('GCX', 'Gaborone Coin Exchange', 'BW', 'Gaborone Coin Exchange (Pty) Ltd'),
    ('AIZ', 'Alpha Direct Insurance Zambia', 'ZM', 'Alpha Direct Insurance Zambia Ltd'),
]


def main():
    with transaction.atomic():
        for code, name, country, legal_name in NEW_ENTITIES:
            obj, created = Company.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'country': country,
                    'legal_name': legal_name,
                    'is_default': False,
                },
            )
            print(f'  {"+" if created else " "} {code}  {name}')

        # Retag existing ADIC JEs (FY25 + FY26 YTD) that were posted without
        # a company. Match by entry_number prefix and the seed source.
        adic = Company.objects.get(code='ADIC')
        legacy_je_descriptions = (
            'FY25 GL — annual closing posting from CFO Odoo export',
            'FY26 YTD GL — 10 months Jul 2025 – Apr 2026 from CFO Odoo export',
        )
        for desc in legacy_je_descriptions:
            for je in JournalEntry.objects.filter(description=desc, company__isnull=True):
                JournalEntry.objects.filter(pk=je.pk).update(company=adic)
                print(f'  retagged {je.entry_number} → company=ADIC')


main()
