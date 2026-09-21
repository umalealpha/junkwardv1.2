# Claim-detail figures on GraphiteClaim (Bokani 2026-06-24): total reserve,
# total payment, balance, date of loss — from GET /api/v1/claims/{id}.
# Hand-written to match integrations/models.py (no local makemigrations env).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0004_graphite_claim'),
    ]

    operations = [
        migrations.AddField(
            model_name='graphiteclaim',
            name='date_of_loss',
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='total_reserve',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='total_payment',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='balance',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=16),
        ),
        migrations.AddField(
            model_name='graphiteclaim',
            name='detail_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
