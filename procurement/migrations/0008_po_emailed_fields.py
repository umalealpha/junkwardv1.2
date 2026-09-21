# Hand-written (CFO claims-PO email flow, 2026-07-07) — ONLY the two
# last_emailed_* AddField ops for the "Sent ✓" stamp on PurchaseOrder.
# makemigrations output deliberately NOT used: it bundles unrelated
# pre-existing model drift (see 0007's header note for the same call).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0007_claimsassessment'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='last_emailed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='last_emailed_to',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
