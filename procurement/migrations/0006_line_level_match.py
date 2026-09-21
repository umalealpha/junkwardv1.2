"""
procurement 0005 — Line-level 3-way match.

Adds:
  - PurchaseOrderLine.qty_billed_to_date  (Decimal default 0)
  - POBillMatchLine model (bill_line ↔ po_line ↔ grn_line)
"""
import uuid
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0005_vendor_kyc'),
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorderline',
            name='qty_billed_to_date',
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal('0.00'),
                help_text='Sum of qty across every POBillMatchLine against this PO line. Used by the over-bill guard in match_bill_lines.',
                max_digits=12,
            ),
        ),
        migrations.CreateModel(
            name='POBillMatchLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('qty', models.DecimalField(decimal_places=4, default=Decimal('0.00'), max_digits=12)),
                ('unit_price', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('line_variance', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('bill_line', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='match_records', to='billing.invoiceline')),
                ('grn_line', models.ForeignKey(blank=True, help_text='Most-recent GRN line for the same PO line at the time of the match. NULL for service bills with no goods receipt.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='bill_match_lines', to='procurement.goodsreceiptnoteline')),
                ('match', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='match_lines', to='procurement.pobillmatch')),
                ('po_line', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='bill_match_lines', to='procurement.purchaseorderline')),
            ],
            options={
                'verbose_name': 'PO ↔ Bill Match Line',
                'verbose_name_plural': 'PO ↔ Bill Match Lines',
                'ordering': ['created_at'],
                'abstract': False,
            },
        ),
        migrations.AddConstraint(
            model_name='pobillmatchline',
            constraint=models.UniqueConstraint(fields=('match', 'bill_line'), name='uq_po_bill_match_line'),
        ),
    ]
