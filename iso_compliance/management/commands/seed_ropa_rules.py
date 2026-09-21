"""
Seed a starter rulebook + legal-basis suggestion rules for the ROPA module
(Developer Build Brief). The DPO maintains these afterwards. Idempotent.

    python manage.py seed_ropa_rules
"""
from django.core.management.base import BaseCommand

from iso_compliance.models import LegalBasisRule, RulebookRequirement

RULEBOOK = [
    ('DPA-s48-details', 'Botswana DPA 2024, s.48', 'A cross-border transfer must be documented.',
     'cross_border_transfer', 'Yes', 'cross_border_details filled'),
    ('DPA-s48-vendor', 'Botswana DPA 2024, s.48', 'A cross-border processor must have a signed DPA.',
     'cross_border_transfer', 'Yes', 'vendor.dpa_contract_signed=True'),
    ('DPA-DPIA', 'Botswana DPA 2024, s.31', 'Where a DPIA is required, its status must be recorded.',
     'dpia_required', 'Yes', 'dpia_status filled'),
    ('DPA-RETENTION', 'Botswana DPA 2024, s.13', 'A retention period must be stated for the activity.',
     'processing_activity', '', 'retention_period filled'),
    ('POL-AIGOV-S9', 'AD-POL-AI-GOV-001, s.9', 'Health data must have security measures recorded.',
     'detected_data_category', 'Health Data', 'security_measures filled'),
]

BASIS_RULES = [
    ('LBR-CLAIMS', 'Claims', ['claim', 'settlement', 'assessment'], 'contract',
     'This looks like {purpose}, which usually relies on performance of a contract.'),
    ('LBR-POLICY', '', ['policy servicing', 'premium', 'renewal', 'underwriting'], 'contract',
     'This looks like {purpose}, part of servicing the policy contract.'),
    ('LBR-MARKETING', '', ['marketing', 'promotion', 'campaign'], 'consent',
     'This looks like {purpose}, which usually relies on the data subject giving consent.'),
    ('LBR-LEGAL', '', ['regulatory', 'nbfira', 'compliance', 'kyc', 'aml', 'tax'], 'legal_obligation',
     'This looks like {purpose}, which is usually a legal obligation.'),
    ('LBR-HR', 'HR', ['payroll', 'employment', 'leave', 'salary'], 'contract',
     'This looks like {purpose}, part of the employment contract.'),
]


class Command(BaseCommand):
    help = 'Seed starter ROPA rulebook + legal-basis rules (idempotent).'

    def handle(self, *args, **opts):
        rb = lb = 0
        for rid, src, desc, tf, tv, ev in RULEBOOK:
            _, c = RulebookRequirement.objects.update_or_create(
                requirement_id=rid,
                defaults={'source': src, 'description': desc, 'trigger_field': tf,
                          'trigger_value': tv, 'satisfying_evidence': ev})
            rb += int(c)
        for rid, dept, kws, basis, tmpl in BASIS_RULES:
            _, c = LegalBasisRule.objects.update_or_create(
                rule_id=rid,
                defaults={'match_department': dept, 'match_purpose_keywords': kws,
                          'suggested_basis': basis, 'reason_template': tmpl})
            lb += int(c)
        self.stdout.write(self.style.SUCCESS(
            f'ROPA rules: {rb} rulebook + {lb} legal-basis rules seeded.'))
