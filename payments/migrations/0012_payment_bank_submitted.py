"""Payment.bank_submitted_at — hard guard against paying a payment twice via
the FNB batch (Fable audit 2026-07-08)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0011_reference_blank'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='bank_submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
