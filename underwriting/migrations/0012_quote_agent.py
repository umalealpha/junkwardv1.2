from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('underwriting', '0011_quoteratefloor_min_premium'),
    ]

    operations = [
        migrations.AddField(
            model_name='quote',
            name='agent',
            field=models.CharField(blank=True, default='', max_length=160),
        ),
        migrations.AddField(
            model_name='quote',
            name='agent_email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
    ]
