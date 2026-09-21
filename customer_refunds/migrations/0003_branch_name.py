from django.db import migrations, models


class Migration(migrations.Migration):
    """Finance asked for a branch NAME field (branch code stays optional) —
    Keetile email 2026-07-28. Received from Graphite on the refund handoff."""

    dependencies = [
        ('customer_refunds', '0002_ai_fraud_review'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerrefund',
            name='branch_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
    ]
