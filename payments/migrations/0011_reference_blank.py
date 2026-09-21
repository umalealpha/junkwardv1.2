"""Reference optional (Fable audit 2026-07-07): the UI marks Reference
optional — an empty reference must not 400 the form."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0010_payment_once_off'),
    ]

    operations = [
        migrations.AlterField(
            model_name='payment',
            name='reference',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]
