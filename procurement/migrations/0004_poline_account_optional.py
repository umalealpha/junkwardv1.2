"""
CFO directive 2026-05-21 — Purchase orders MUST NOT be linked to a GL
account. Make ``PurchaseOrderLine.account`` nullable so new POs can be
raised without picking an account. The bill that matches the PO carries
the GL line on its own; the PO is purely a commitment + ordering record.

Existing rows keep their populated account FK (no data loss).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0001_initial'),
        ('procurement', '0003_phase_b_manus_audit'),
    ]

    operations = [
        migrations.AlterField(
            model_name='purchaseorderline',
            name='account',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=models.deletion.PROTECT,
                related_name='po_lines',
                to='ledger.account',
                help_text='Legacy field. New POs are no longer tied to a GL '
                          'account — the bill carries the GL line.',
            ),
        ),
    ]
