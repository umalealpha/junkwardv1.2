"""
iso_compliance/seed.py — the 10 commandments + their ISO 27001:2022 mappings.

Loaded by `python manage.py iso_audit --seed` (idempotent — number is the
unique key, so re-runs update titles/clauses but never duplicate rows).
"""
from __future__ import annotations

from .models import Commandment


TEN_COMMANDMENTS = [
    {
        'number': 1,
        'title': 'Thou shalt control identity and access',
        'summary': (
            'Every user authenticated through SSO/MFA; least-privilege roles; '
            'no shared accounts; access reviewed quarterly.'
        ),
        'iso_clauses': 'A.5.15, A.5.16, A.5.17, A.5.18, A.8.2, A.8.3, A.8.5',
        'why_it_matters': (
            'Identity is the #1 attack surface for an insurer. ISO requires '
            'documented joiner/mover/leaver, MFA on all admin paths, and '
            'periodic access reviews. Failure = audit non-conformance + '
            'NBFIRA prudential finding.'
        ),
        'owner': 'CFO + IT Lead',
    },
    {
        'number': 2,
        'title': 'Thou shalt log every change',
        'summary': (
            'Immutable audit log of who did what, when, from where — '
            'GL postings, approvals, user-management, exports.'
        ),
        'iso_clauses': 'A.5.28, A.8.15, A.8.16, A.8.17',
        'why_it_matters': (
            'Without logs we cannot prove integrity to auditors or '
            'reconstruct an incident. Logs must be append-only and retained '
            '≥12 months.'
        ),
        'owner': 'CFO',
    },
    {
        'number': 3,
        'title': 'Thou shalt encrypt secrets and data',
        'summary': (
            'TLS in transit, encrypted at rest, secrets in env not in code, '
            'key rotation policy, no PAT in chat or repo.'
        ),
        'iso_clauses': 'A.5.33, A.8.24',
        'why_it_matters': (
            'DPA Botswana + ISO require encryption for personal/financial '
            'data. A leaked DB password or API key is a reportable breach.'
        ),
        'owner': 'IT Lead',
    },
    {
        'number': 4,
        'title': 'Thou shalt back up and prove recovery',
        'summary': (
            'Daily encrypted backups, off-instance copy, RPO ≤ 24h, RTO ≤ 8h, '
            'restore tested at least quarterly.'
        ),
        'iso_clauses': 'A.5.29, A.5.30, A.8.13, A.8.14',
        'why_it_matters': (
            'An untested backup is not a backup. ISO + BoB business-continuity '
            'guidance require evidence of a successful restore drill.'
        ),
        'owner': 'IT Lead',
    },
    {
        'number': 5,
        'title': 'Thou shalt manage change through code review',
        'summary': (
            'Every prod change passes PR + review; no direct production edits; '
            'migrations versioned; rollback documented.'
        ),
        'iso_clauses': 'A.8.32, A.8.33, A.8.31',
        'why_it_matters': (
            'Unreviewed prod hotfixes are the leading cause of self-inflicted '
            'outages and silent data corruption.'
        ),
        'owner': 'CFO + Dev Lead',
    },
    {
        'number': 6,
        'title': 'Thou shalt respond to incidents and learn',
        'summary': (
            'Incident register, severity scale, on-call escalation, postmortem '
            'within 5 working days, action items tracked to closure.'
        ),
        'iso_clauses': 'A.5.24, A.5.25, A.5.26, A.5.27, A.6.8',
        'why_it_matters': (
            'A reportable cyber/operational event without a documented '
            'response is automatic audit failure.'
        ),
        'owner': 'CFO',
    },
    {
        'number': 7,
        'title': 'Thou shalt vet thy suppliers',
        'summary': (
            'Vendor register, DPA/NDA on file, data-flow mapped, key rotation '
            'on contractor exit, sub-processor list reviewed annually.'
        ),
        'iso_clauses': 'A.5.19, A.5.20, A.5.21, A.5.22, A.5.23',
        'why_it_matters': (
            'Most breaches now come through suppliers (Odoo migrator, IT '
            'contractors, payment gateways). ISO holds the controller '
            '(ADIC) responsible for the processor.'
        ),
        'owner': 'CFO',
    },
    {
        'number': 8,
        'title': 'Thou shalt know thine assets and data',
        'summary': (
            'Asset register (hw/sw/data), data classification (public / '
            'internal / confidential / PII), owners assigned.'
        ),
        'iso_clauses': 'A.5.9, A.5.10, A.5.11, A.5.12, A.5.13, A.8.1',
        'why_it_matters': (
            'You cannot protect what you have not inventoried. Required for '
            'NBFIRA materiality assessments too.'
        ),
        'owner': 'CFO + IT Lead',
    },
    {
        'number': 9,
        'title': 'Thou shalt plan for continuity',
        'summary': (
            'BCP/DR plan covering omni outage, payment-gateway failure, '
            'key-person absence; tested annually; communicated.'
        ),
        'iso_clauses': 'A.5.29, A.5.30',
        'why_it_matters': (
            'Insurance is a critical national service. NBFIRA expects a '
            'tested DR runbook; ISO formalises it.'
        ),
        'owner': 'CFO',
    },
    {
        'number': 10,
        'title': 'Thou shalt comply and monitor compliance',
        'summary': (
            'DPA (BW) registration, NBFIRA conduct compliance, IFRS 17 '
            'controls, recurring management review, evidence pack '
            'maintained for audit.'
        ),
        'iso_clauses': 'A.5.31, A.5.32, A.5.34, A.5.35, A.5.36, A.5.37',
        'why_it_matters': (
            'Compliance is a continuous control, not an annual sprint. '
            'A live evidence register cuts audit time by 80% and prevents '
            'last-minute scramble.'
        ),
        'owner': 'CFO',
    },
]


def seed_commandments() -> int:
    """Idempotent. Returns number of rows created."""
    created = 0
    for row in TEN_COMMANDMENTS:
        obj, was_created = Commandment.objects.update_or_create(
            number=row['number'],
            defaults={
                'title':          row['title'],
                'summary':        row['summary'],
                'iso_clauses':    row['iso_clauses'],
                'why_it_matters': row['why_it_matters'],
                'owner':          row['owner'],
            },
        )
        if was_created:
            created += 1
    return created
