from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agent_portal', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='agent_code',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AlterField(
            model_name='agentbankaccount',
            name='bank_name',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
    ]
