"""
Seed the Policy & Legal Framework Library from the DPO's mapping
(iso_compliance/data/dpa2024_requirements.json, generated from
DPA_2024_requirements.xlsx — 73 Botswana DPA 2024 requirements).

    python manage.py seed_dpa_requirements

Idempotent. Upserts FrameworkRequirement by code; parses each row's
'Policy: X; Y' text into InternalPolicy placeholders + RequirementPolicyLink
rows (confirmed=False — the DPO confirms the auto-parsed links). Never trusts
the free-text parse blindly: (Section/Clause X) -> specific_section, and a
'NOTE ... gap' annotation flags the policy as a gap.
"""
import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from iso_compliance.models import (FrameworkRequirement, InternalPolicy,
                                    RequirementPolicyLink)

DATA = Path(__file__).resolve().parents[2] / 'data' / 'dpa2024_requirements.json'
VALID_CAT = {c for c, _ in FrameworkRequirement.CATEGORY_CHOICES}
STATUS_HINTS = [
    (re.compile(r'in progress', re.I), 'under_review'),
    (re.compile(r'not yet approved', re.I), 'under_review'),
    (re.compile(r'active document|maintained separately', re.I), 'published'),
]


def _parse_policy(part: str):
    """(base_name, specific_section, is_gap, gap_note, status_hint) from one mention."""
    raw = part.strip().strip('.').strip()
    is_gap, gap_note, section, status = False, '', '', ''
    # gap note: "— NOTE: ... (gap)" or trailing "(gap)"
    mnote = re.search(r'[—-]\s*NOTE:\s*(.+)$', raw, re.I)
    if mnote or 'gap' in raw.lower():
        is_gap = True
        gap_note = (mnote.group(1).strip() if mnote else raw)[:300]
        raw = raw[:mnote.start()].strip() if mnote else re.sub(r'\(gap\)', '', raw, flags=re.I).strip()
    # status hint parentheticals
    for rx, st in STATUS_HINTS:
        if rx.search(raw):
            status = st
    # specific section: (Section 9) / (Clause 3.7)
    msec = re.search(r'\((?:Section|Clause)\s+([^)]+)\)', raw, re.I)
    if msec:
        section = msec.group(0)[1:-1].strip()
    # strip ALL trailing parentheticals to get the base name
    base = re.sub(r'\s*\([^)]*\)\s*$', '', raw).strip()
    base = re.sub(r'\s*\([^)]*\)\s*$', '', base).strip()  # a 2nd, in case of two
    base = re.sub(r'\s+', ' ', base).strip(' ;.-—')
    return base, section, is_gap, gap_note, status


class Command(BaseCommand):
    help = 'Seed the Policy & Legal Framework Library (73 DPA 2024 requirements + links).'

    @transaction.atomic
    def handle(self, *args, **opts):
        rows = json.loads(DATA.read_text())
        req_created = req_updated = pol_created = link_created = 0
        for row in rows:
            code = (row.get('requirement_code') or '').strip()
            if not code:
                continue
            cat = row.get('category') if row.get('category') in VALID_CAT else 'principles'
            obj, created = FrameworkRequirement.objects.update_or_create(
                requirement_code=code,
                defaults={
                    'requirement_name': row.get('requirement_name', ''),
                    'plain_description': row.get('plain_description', ''),
                    'category': cat,
                    'source_act': row.get('source_act', 'Botswana DPA 2024'),
                },
            )
            req_created += int(created)
            req_updated += int(not created)
            # parse policy links
            praw = row.get('policy_raw', '') or ''
            for part in re.split(r';|\n', praw):
                if not part.strip():
                    continue
                base, section, is_gap, gap_note, status = _parse_policy(part)
                if not base or len(base) < 3:
                    continue
                pol, pcreated = InternalPolicy.objects.get_or_create(
                    policy_name=base,
                    defaults={'is_placeholder': True, 'is_gap': is_gap,
                              'gap_note': gap_note, 'status': status or 'draft'},
                )
                pol_created += int(pcreated)
                if not pcreated:
                    changed = False
                    if is_gap and not pol.is_gap:
                        pol.is_gap = True; pol.gap_note = gap_note or pol.gap_note; changed = True
                    if status and pol.status == 'draft':
                        pol.status = status; changed = True
                    if changed:
                        pol.save(update_fields=['is_gap', 'gap_note', 'status'])
                _, lcreated = RequirementPolicyLink.objects.get_or_create(
                    requirement=obj, policy=pol, specific_section=section,
                    defaults={'confirmed': False},
                )
                link_created += int(lcreated)
        self.stdout.write(self.style.SUCCESS(
            f'Framework: {req_created} requirements created, {req_updated} updated; '
            f'{pol_created} placeholder policies; {link_created} links (unconfirmed).'))
