"""Contact.company FK + backfill every existing row to ADIC.

CFO directive 2026-05-18: vendors/customers belong to a single subsidiary.
The topbar company switcher MUST filter the Contact list. Every existing
row was created before the field existed and is implicitly an
Alpha Direct Insurance Company Botswana (ADIC) record per CFO statement
('All of these vendors belong exclusively to ADIC').

We backfill to ADIC, then the CFO uploads separate ADSA / RSA / VCM
vendor lists which the importer stamps with the right company on insert.
"""
import django.db.models.deletion
from django.db import migrations, models


def _backfill_company_adic(apps, schema_editor):
    Contact = apps.get_model('billing', 'Contact')
    Company = apps.get_model('core', 'Company')
    adic = Company.objects.filter(code__iexact='ADIC').first()
    if not adic:
        # No ADIC seeded yet (fresh install / test DB) — nothing to backfill.
        return
    Contact.objects.filter(company__isnull=True).update(company=adic)


def _noop_reverse(apps, schema_editor):
    """Reverse migration leaves the FK populated — safe to no-op."""
    return


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0006_contact_external_ref'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='company',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='contacts',
                to='core.company',
                help_text='Owning legal entity. Filters apply per-company in '
                          'the topbar switcher.',
            ),
        ),
        migrations.RunPython(_backfill_company_adic, _noop_reverse),
    ]
