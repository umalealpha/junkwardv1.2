from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0021_paymentloadoverride'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentrequest',
            name='loaded_off_window',
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
