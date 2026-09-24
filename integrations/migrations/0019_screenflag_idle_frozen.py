# Generated for the idle-frozen screen rule (2026-09-21).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0018_claims_lifecycle_b1_b14'),
    ]

    operations = [
        migrations.AddField(
            model_name='screenintegrityflag',
            name='idle_frozen_pct',
            field=models.DecimalField(decimal_places=1, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name='screenintegrityflag',
            name='idle_frozen_hours',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=6),
        ),
    ]
