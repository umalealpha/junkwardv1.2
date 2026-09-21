"""Seed the live register from the frozen Capital Story constants.

Idempotent: skips if any Stakeholder already exists, so re-running deploy never
double-seeds. Best-effort links each ESOP grantee to their payroll.Employee by
name so the self-service My-Equity page resolves; unmatched grantees (external
or renamed) are left unlinked for Finance to link in the UI.
"""
from __future__ import annotations

from django.db import migrations


def seed(apps, schema_editor):
    Stakeholder = apps.get_model('equity', 'Stakeholder')
    ShareHolding = apps.get_model('equity', 'ShareHolding')
    EsopGrant = apps.get_model('equity', 'EsopGrant')
    if Stakeholder.objects.exists():
        return

    from reporting import equity_data

    Employee = apps.get_model('payroll', 'Employee')

    def find_employee(name: str):
        # EXACT, UNIQUE match only. A grant drives a private "My Equity" view, so
        # a wrong link would show one person another's holdings — never risk that
        # on a fuzzy match. Ambiguous or no-exact-match is left unlinked for
        # Finance to link deliberately in the UI.
        exact = list(Employee.objects.filter(full_name__iexact=name)[:2])
        return exact[0] if len(exact) == 1 else None

    # Shareholders (cap table). These are individuals/entities; entities have no
    # staff record. Kind is a best guess from the note; Finance can correct it.
    entity_hints = ('ventures', 'holdings', 'capital', 'establishment', 'oak',
                    'winterhold', 'launch africa', 'fexty', 'khumo', 'ntja', 'vedanta')
    for row in equity_data.CAP_TABLE:
        name = row['holder']
        kind = 'entity' if any(h in name.lower() for h in entity_hints) else 'individual'
        s = Stakeholder.objects.create(
            name=name, kind=kind, note=row.get('note', '') or '',
            employee=(find_employee(name) if kind == 'individual' else None),
        )
        ShareHolding.objects.create(
            stakeholder=s, klass=row.get('klass', '') or '',
            shares=row.get('shares', 0) or 0,
            usd_invested=row.get('usd', 0) or 0,
            note=row.get('note', '') or '',
        )

    # ESOP option holders. All are staff (or ex-staff) — try to link each.
    for g in equity_data.ESOP.get('grants', []):
        name = g['grantee']
        s = Stakeholder.objects.filter(name__iexact=name).first()
        if s is None:
            s = Stakeholder.objects.create(
                name=name, kind='individual', employee=find_employee(name),
            )
        if s.employee is None:
            emp = find_employee(name)
            if emp:
                s.employee = emp
                s.save(update_fields=['employee'])
        EsopGrant.objects.create(
            stakeholder=s, units=g.get('units', 0) or 0,
            pct_pool=g.get('pct_pool', 0) or 0,
            pct_fd=g.get('pct_fd', 0) or 0,
            status='active',
            note='Seeded from Carta snapshot 31 Mar 2026.',
        )


def unseed(apps, schema_editor):
    # Only remove the seed if it is untouched (no vesting added, no edits since).
    Stakeholder = apps.get_model('equity', 'Stakeholder')
    VestingTranche = apps.get_model('equity', 'VestingTranche')
    if VestingTranche.objects.exists():
        return
    Stakeholder.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('equity', '0001_initial'),
        ('payroll', '0021_employee_archive'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
