from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0033_letterrequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='letterrequest',
            name='additional_details',
            field=models.CharField(blank=True, default='', max_length=1000),
        ),
    ]
