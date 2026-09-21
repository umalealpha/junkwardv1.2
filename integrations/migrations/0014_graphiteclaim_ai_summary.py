"""Claim insight AI fields on GraphiteClaim (CFO 2026-08-31, Claim-Description-Spec).
Stores the off-peak, advisory-only AI summary + soft pay/hold opinion per claim.
Additive only — no data change, no GL involvement."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0013_event_idempotency_attribution'),
    ]

    operations = [
        migrations.AddField(
            model_name='graphiteclaim',
            name='ai_summary',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='ai_suggestion',
            field=models.CharField(blank=True, default='', max_length=8),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='ai_reason',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='ai_engine',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='ai_summary_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
