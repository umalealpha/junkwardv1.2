#!/usr/bin/env python
"""
seed_group_companies.py

Creates the missing Alpha Direct group entity Company records so the
company switcher in the dashboard can offer real subsidiaries instead
of only Alpha Direct Insurance.

Run:
    python manage.py shell < ops/seeds/seed_group_companies.py

Idempotent: skips any code that already exists.

Note: the codes here match what the HRIS HTML's COMPANIES array expects
(ADIC, RSA, ADSA, UNI, QIH, VCM, ADRG), so multi-company HR + finance
views can share the same identifiers across the stack.
"""
import os, sys, django
from django.apps import apps as _django_apps
if not _django_apps.ready:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'alpha_finance.settings_bot')
    django.setup()

from core.models import Company


ENTITIES = [
    # (code, name, country_iso2, legal_name)
    ('ADIC',  'Alpha Direct Insurance Co.',          'BW', 'Alpha Direct Insurance Co. (Pty) Ltd'),
    ('ADIIC', 'Alpha Direct Insurtech',              'BW', 'Alpha Direct Insurtech (Pty) Ltd'),
    ('ADIH',  'Alpha Direct Insurance Holdings',     'BW', 'Alpha Direct Insurance Holdings (Pty) Ltd'),
    ('ADIL',  'Alpha Direct Insurance Life',         'BW', 'Alpha Direct Insurance Life (Pty) Ltd'),
    ('ADSA',  'Alpha Direct South Africa',           'ZA', 'Alpha Direct South Africa (Pty) Ltd'),
    ('ADRG',  'ADRisk Global Solutions',             'BW', 'ADRisk Global Solutions (Pty) Ltd'),
    ('RSA',   'Risksoftware Africa',                 'BW', 'Risksoftware Africa (Pty) Ltd'),
    ('UNI',   'Unicoin',                             'BW', 'Unicoin (Pty) Ltd'),
    ('QIH',   'Quantum Insurance Holdings',          'BW', 'Quantum Insurance Holdings (Pty) Ltd'),
    ('VCM',   'Veritas Capital Management',          'BW', 'Veritas Capital Management (Pty) Ltd'),
]


def _rename_existing():
    """The first deploy may have created a Company with code='ADI'. Rename
    to 'ADIC' to match the rest of the stack so the switcher works."""
    legacy = Company.objects.filter(code='ADI').first()
    if legacy and not Company.objects.filter(code='ADIC').exists():
        legacy.code = 'ADIC'
        legacy.save()
        print(f'  Renamed Company code ADI -> ADIC (id={legacy.id})')


def _set_agency_relationships():
    """Unicoin is an agency of Alpha Direct Insurance Co. (CFO directive 2026-05-12).
    Unicoin's transactions roll up to ADIC for statutory reporting."""
    adic = Company.objects.filter(code='ADIC').first()
    uni = Company.objects.filter(code='UNI').first()
    if adic and uni and uni.parent_company_id != adic.id:
        uni.parent_company = adic
        uni.save()
        print(f'  set   UNI.parent_company = ADIC (agency relationship)')


def main():
    _rename_existing()
    created = 0
    skipped = 0
    for code, name, country, legal_name in ENTITIES:
        if Company.objects.filter(code=code).exists():
            skipped += 1
            print(f'  skip  {code}  (already exists)')
            continue
        Company.objects.create(
            code=code,
            name=name,
            legal_name=legal_name,
            country=country,
            is_active=True,
            is_default=(code == 'ADIC'),
        )
        created += 1
        print(f'  +     {code}  {name}')
    _set_agency_relationships()
    print()
    print(f'Done. Created {created}, skipped {skipped}.')
    print(f'Total Company rows in DB now: {Company.objects.count()}')


main()
