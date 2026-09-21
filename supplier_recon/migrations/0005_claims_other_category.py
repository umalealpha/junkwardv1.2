"""Move seeded claims suppliers onto the claim-backed category.

Fable 5 review 2026-07-25. ``classify_recon_suppliers`` seeded every
claims-heavy vendor as ``other``, but ``other`` was not in
``CLAIM_BACKED_CATEGORIES`` — so the claim-authorisation rule could never fire
and the CFO's "No claim authority" tile was a guaranteed zero across all 31
claim suppliers on prod.

The fix introduces a distinct ``claims_other`` ("Claims Supplier — trade
unconfirmed"), which IS claim-backed, leaving plain ``other`` non-claim-backed
so a human picking "Other" for a stationery supplier does not inherit the rule.

This migration re-points the rows the command seeded. It only touches profiles
whose notes show the command created them — a category a person chose by hand is
never overwritten.
"""

from django.db import migrations

SEEDED_MARKER = 'Seeded by classify_recon_suppliers'


def forwards(apps, schema_editor):
    Profile = apps.get_model('supplier_recon', 'ReconSupplierProfile')
    moved = (Profile.objects
             .filter(category='other', notes__startswith=SEEDED_MARKER)
             .update(category='claims_other'))
    if moved:
        print(f'  supplier_recon: {moved} seeded claims supplier(s) '
              f'other -> claims_other (claim-authorisation rule now applies)')


def backwards(apps, schema_editor):
    Profile = apps.get_model('supplier_recon', 'ReconSupplierProfile')
    (Profile.objects
     .filter(category='claims_other', notes__startswith=SEEDED_MARKER)
     .update(category='other'))


class Migration(migrations.Migration):

    dependencies = [
        ('supplier_recon', '0004_bill_not_posted_reason'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
