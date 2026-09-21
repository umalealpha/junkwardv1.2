"""
0002_cession_invoice_link — auto-cession backbone.

Adds:
  - Cession.invoice (FK -> billing.Invoice, nullable)
  - Cession.share_percent (snapshot of treaty share at the time of the run)
  - Index on (invoice, treaty) for the idempotency lookup
  - UniqueConstraint on (invoice, treaty) WHERE invoice IS NOT NULL —
    re-runs of run_cession_pass() are safe.
"""

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0008_payment_terms'),
        ('reinsurance', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cession',
            name='invoice',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='reinsurance_cessions',
                to='billing.invoice',
                help_text='Source customer invoice this cession was generated from '
                          'by the auto-cession pass. NULL for manual / bordereau cessions.',
            ),
        ),
        migrations.AddField(
            model_name='cession',
            name='share_percent',
            field=models.DecimalField(
                blank=True, null=True,
                decimal_places=4, max_digits=6,
                help_text='Cession % applied at the time of the auto-cession run. '
                          'Snapshot from treaty.cession_share_percent.',
            ),
        ),
        migrations.AddIndex(
            model_name='cession',
            index=models.Index(
                fields=['invoice', 'treaty'],
                name='cession_invoice_treaty_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='cession',
            constraint=models.UniqueConstraint(
                fields=['invoice', 'treaty'],
                condition=models.Q(invoice__isnull=False),
                name='uniq_cession_invoice_treaty',
            ),
        ),
    ]
