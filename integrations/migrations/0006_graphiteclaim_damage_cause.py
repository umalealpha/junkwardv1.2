from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0005_graphiteclaim_detail_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='graphiteclaim',
            name='damage_cause',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
    ]
