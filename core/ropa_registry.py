"""
core/ropa_registry.py — the ROPA module engines (Developer Build Brief, DPO
Oratile 2026-09-08). Detection -> draft entries; lawful-basis SUGGESTION; vendor
risk SCORING; rulebook FLAGGING. Suggestions never self-finalise — the DPO
confirms lawful basis and risk level.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from iso_compliance.models import (LegalBasisRule, RopaEntry, RulebookRequirement,
                                   VendorRegister)
from core.ropa_field_scan import _scan

# detected field category -> ROPA data category label
CAT_LABEL = {
    'banking': 'Banking Details', 'identity': 'Government ID / Identity',
    'health': 'Health Data', 'contact': 'Contact Information',
}
SENSITIVE_CATS = {'Banking Details', 'Government ID / Identity', 'Health Data'}


def detect_and_create_entries():
    """Create a draft RopaEntry for every detected personal-data field not already
    tracked. Returns (created, skipped)."""
    existing = set(RopaEntry.objects.values_list('source_table', 'source_field'))
    created = skipped = 0
    for d in _scan():
        key = (d['model_name'], d['field_name'])
        if key in existing:
            skipped += 1
            continue
        RopaEntry.objects.create(
            source_table=f"{d['app_label']}.{d['model_name']}",
            source_field=d['field_name'],
            detected_data_category=CAT_LABEL.get(d['category'], 'Unclassified — needs manual review'),
            flag_status=RopaEntry.FLAG_DRAFT,
        )
        created += 1
        existing.add(key)
    return created, skipped


def suggest_legal_basis(entry: RopaEntry):
    """Populate suggested_lawful_basis + reason from LegalBasisRule. Never touches
    confirmed_lawful_basis."""
    if entry.confirmed_lawful_basis:
        return
    purpose = (entry.purpose_of_processing or '').lower()
    dept = (entry.department or '').strip().lower()
    for rule in LegalBasisRule.objects.all():
        if rule.match_department and rule.match_department.strip().lower() != dept:
            continue
        kws = [k.lower() for k in (rule.match_purpose_keywords or [])]
        if kws and not any(k in purpose for k in kws):
            continue
        cite = ''
        if rule.framework_requirement_id:
            cite = f' — see {rule.framework_requirement_id}'
        entry.suggested_lawful_basis = rule.suggested_basis
        entry.suggested_lawful_basis_reason = (
            rule.reason_template.replace('{purpose}', entry.purpose_of_processing or 'this activity')
            + cite)
        return
    entry.suggested_lawful_basis = ''
    entry.suggested_lawful_basis_reason = 'No suggestion available — needs manual determination.'


def compute_vendor_risk(vendor: VendorRegister):
    """Weighted score -> band. Stores itemised breakdown. Never sets confirmed."""
    factors = []
    score = 0
    if not vendor.dpa_contract_signed:
        score += 30; factors.append({'factor': 'No signed DPA / contract', 'points': 30})
    if vendor.sub_processors_used:
        score += 15; factors.append({'factor': 'Sub-processors used', 'points': 15})
    linked = list(vendor.ropa_entries.all())
    if any(e.cross_border_transfer == 'Yes' for e in linked):
        score += 20; factors.append({'factor': 'Cross-border transfer on a linked activity', 'points': 20})
    if any(e.detected_data_category in SENSITIVE_CATS for e in linked):
        score += 25; factors.append({'factor': 'Sensitive data category (health/banking/ID)', 'points': 25})
    if vendor.last_review_date and vendor.last_review_date < (timezone.localdate() - timedelta(days=365)):
        score += 10; factors.append({'factor': 'Review older than 12 months', 'points': 10})
    elif not vendor.last_review_date:
        score += 10; factors.append({'factor': 'Never reviewed', 'points': 10})
    level = 'Low' if score < 30 else ('Medium' if score < 60 else 'High')
    vendor.suggested_risk_score = score
    vendor.suggested_risk_reason = factors
    vendor.suggested_risk_level = level
    return score, level, factors


def _evidence_met(entry: RopaEntry, hint: str) -> bool:
    """Machine check of a rulebook satisfying_evidence hint:
       '<field> filled'            -> that entry field is non-empty
       'vendor.<field>=<value>'    -> linked vendor field equals value
       'vendor.<field> filled'     -> linked vendor field non-empty"""
    hint = (hint or '').strip()
    if not hint:
        return True
    if hint.startswith('vendor.'):
        rest = hint[len('vendor.'):]
        v = entry.processor_vendor
        if v is None:
            return False
        if rest.endswith(' filled'):
            f = rest[:-len(' filled')].strip()
            return bool(getattr(v, f, '') )
        if '=' in rest:
            f, val = rest.split('=', 1)
            got = getattr(v, f.strip(), None)
            return str(got).strip().lower() == val.strip().lower()
        return False
    if hint.endswith(' filled'):
        f = hint[:-len(' filled')].strip()
        return bool(getattr(entry, f, ''))
    return True


def evaluate_flags(entry: RopaEntry, save=True):
    """Recompute flag_status + flag_reasons. flagged_incomplete if any triggered
    rulebook rule's evidence is unmet; confirmed if all required human fields
    (incl. confirmed_lawful_basis) are filled; else draft_needs_review."""
    reasons = []
    for rule in RulebookRequirement.objects.all():
        tf = rule.trigger_field.strip()
        if not tf:
            continue
        val = getattr(entry, tf, '')
        triggered = (str(val).strip().lower() == rule.trigger_value.strip().lower()
                     if rule.trigger_value else bool(val))
        if triggered and not _evidence_met(entry, rule.satisfying_evidence):
            reasons.append(rule.requirement_id)

    required_filled = all([
        entry.department, entry.processing_activity, entry.purpose_of_processing,
        entry.confirmed_lawful_basis,
    ])
    if reasons:
        entry.flag_status = RopaEntry.FLAG_INCOMPLETE
    elif required_filled:
        entry.flag_status = RopaEntry.FLAG_CONFIRMED
    else:
        entry.flag_status = RopaEntry.FLAG_DRAFT
    entry.flag_reasons = reasons
    if save:
        entry.save(update_fields=['flag_status', 'flag_reasons', 'date_last_reviewed'])
    return entry.flag_status, reasons
