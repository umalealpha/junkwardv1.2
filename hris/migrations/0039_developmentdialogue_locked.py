from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0038_dd_periods'),
    ]

    operations = [
        migrations.AddField(
            model_name='developmentdialogue',
            name='locked',
            field=models.BooleanField(default=False),
        ),
    ]
