"""Seed the legal office's rate cards and the comparison map.

These are reference data, not transactions: the internal tariff schedule
(ADI-HC-LEGAL-TARIFF-2026-002), the external panel tariff, and the map that says
which of ours is comparable to which of theirs. They came from the guide the
Claims Legal Office sent the CFO on 18 Aug 2026.

Idempotent by design — it seeds only what is missing, matched on the natural key
(scope + item for a tariff line, the fee description for a mapping), so running
it twice cannot duplicate a rate card and cannot overwrite a rate the officer has
since corrected on screen. Correcting a rate is a screen job, not a re-seed.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from bonu.models import LegalRateMapping, LegalSettings, LegalTariffItem

INTERNAL = [
    ('A. Member Consultations & Instructions', 'Initial Legal Consultation — BONU Member', 'Flat rate', 200),
    ('A. Member Consultations & Instructions', 'Initial Consultation — Time-based (cap 2 hrs)', 'Per hour', 35),
    ('A. Member Consultations & Instructions', 'Subsequent Consultation / Follow-up', 'Flat rate', 60),
    ('B. Drafting — Correspondence & Demand Letters', 'Letter of Demand — High Court Matter', 'Flat rate', 280),
    ('B. Drafting — Correspondence & Demand Letters', 'Letter of Demand — Magistrates Court Matter', 'Flat rate', 200),
    ('B. Drafting — Correspondence & Demand Letters', 'Written Correspondence — High Court Matter', 'Flat rate', 63),
    ('B. Drafting — Correspondence & Demand Letters', 'Written Correspondence — Magistrates Court', 'Flat rate', 21),
    ('C. Legal Instruments & Agreements', 'Settlement Agreement / AOD — High Court', 'Flat rate', 280),
    ('C. Legal Instruments & Agreements', 'Settlement Agreement / AOD — Magistrates Court', 'Flat rate', 200),
    ('C. Legal Instruments & Agreements', 'Filing of Notice and Addendums', 'Flat rate', 10),
    ('C. Legal Instruments & Agreements', 'Other Non-Litigious Application (pre-approved by HC Head)', 'Flat rate', 140),
    ('D. Research & Case Preparation (cap 3 hrs/matter)', 'Legal Research and Case Preparation', 'Per hour', 300),
]

EXTERNAL = [
    ('1. Disbursements', 'Telephonic conversation — cellphone', 'Per minute', 2),
    ('1. Disbursements', 'Telephonic conversation — landline', 'Per minute', 1),
    ('1. Disbursements', 'Email', 'Per correspondence', 5),
    ('1. Disbursements', 'Photocopying', 'Per page', 1),
    ('1. Disbursements', 'Fax', 'Per page', 1),
    ('1. Disbursements', 'Mileage', 'Per km', 2),
    ('2. Instructions / Consultation', 'Initial Consultation with client', 'Flat rate', 500),
    ('2. Instructions / Consultation', 'Initial Consultation — time-based (cap 2 hours)', 'Per hour', 2250),
    ('2. Instructions / Consultation', 'Subsequent Consultation', 'Flat rate', 300),
    ('3. Perusal', 'Perusal of appearance/plea/notice/affidavit/other documents', 'Per page', 7),
    ('4. Drafting (cap max 3 hours)', 'Letter of Demand — High Court Matter', 'Flat rate', 700),
    ('4. Drafting (cap max 3 hours)', 'Letter of Demand — Magistrates Court', 'Flat rate', 500),
    ('4. Drafting (cap max 3 hours)', 'Written Correspondence — High Court Matter', 'Flat rate', 315),
    ('4. Drafting (cap max 3 hours)', 'Written Correspondence — Magistrates Court', 'Flat rate', 105),
    ('4. Drafting (cap max 3 hours)', 'Settlement Agreement / AOD — High Court', 'Flat rate', 700),
    ('4. Drafting (cap max 3 hours)', 'Settlement Agreement / AOD — Magistrates Court', 'Flat rate', 500),
    ('4. Drafting (cap max 3 hours)', 'Application for Default Judgment — High Court', 'Flat rate', 1000),
    ('4. Drafting (cap max 3 hours)', 'Application for Default Judgment — Magistrates', 'Flat rate', 700),
    ('4. Drafting (cap max 3 hours)', 'Filing notice and addendums', 'Flat rate', 50),
    ('6. Miscellaneous (capped at 3 hours)', 'Research and case preparation', 'Per hour', 2250),
]

# (fee description, internal unit, internal rate, external item, external unit,
#  external rate, basis, is_disbursement)
MAPPINGS = [
    ('Initial consultation', 'Flat', 200, 'Initial Consultation with client', 'Flat rate', 500, 'flat', False),
    ('Time based', 'Per hour', 35, 'Initial Consultation — time-based (cap 2 hrs)', 'Per hour', 2250, 'per_hour', False),
    ('Perusal of File', 'Per hour', 300, 'Research and case preparation (closest hourly equivalent)', 'Per hour', 2250, 'per_hour', False),
    ('Legal research', 'Per hour', 300, 'Research and case preparation', 'Per hour', 2250, 'per_hour', False),
    ('Electronic mail prepared and sent', 'Per hour', 300, 'Email (per correspondence disbursement)', 'Per correspondence', 5, 'flat', True),
    ('Called Defendant', 'Per hour', 300, 'Telephonic conversation — cellphone (P2/min = P120/hr)', 'Per hour (converted)', 120, 'per_hour', True),
    ('Subsequent Consultation / Follow-up', 'Flat', 60, 'Subsequent Consultation', 'Flat rate', 300, 'flat', False),
    ('Letter of Demand — Magistrates Court Matter', 'Flat', 200, 'Letter of Demand — Magistrates Court', 'Flat rate', 500, 'flat', False),
    ('Letter of Demand — High Court Matter', 'Flat', 280, 'Letter of Demand — High Court Matter', 'Flat rate', 700, 'flat', False),
    ('Settlement Agreement / AOD — Magistrates Court', 'Flat', 200, 'Settlement Agreement / AOD — Magistrates Court', 'Flat rate', 500, 'flat', False),
    ('Settlement Agreement / AOD — High Court', 'Flat', 280, 'Settlement Agreement / AOD — High Court', 'Flat rate', 700, 'flat', False),
]


class Command(BaseCommand):
    help = 'Seed the BONU legal office rate cards, comparison map and settings.'

    def handle(self, *args, **options):
        LegalSettings.solo()
        made_t = made_m = 0
        for scope, rows in (('internal', INTERNAL), ('external', EXTERNAL)):
            for i, (section, item, unit, rate) in enumerate(rows):
                _, created = LegalTariffItem.objects.get_or_create(
                    scope=scope, item=item,
                    defaults={'section': section, 'unit': unit,
                              'rate': Decimal(str(rate)), 'position': i})
                made_t += int(created)
        for i, (desc, iu, ir, ei, eu, er, basis, disb) in enumerate(MAPPINGS):
            _, created = LegalRateMapping.objects.get_or_create(
                fee_description=desc,
                defaults={'internal_unit': iu, 'internal_rate': Decimal(str(ir)),
                          'external_item': ei, 'external_unit': eu,
                          'external_rate': Decimal(str(er)), 'calc_basis': basis,
                          'is_disbursement': disb, 'position': i})
            made_m += int(created)
        self.stdout.write(self.style.SUCCESS(
            f'Legal office reference data ready — {made_t} tariff lines and '
            f'{made_m} mappings added; existing rows left untouched.'))
