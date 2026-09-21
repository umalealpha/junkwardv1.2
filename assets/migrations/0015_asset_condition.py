from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0014_assetcontrolpolicy_it_officer_emails'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='condition',
            field=models.CharField(
                choices=[('functional', 'Functional'), ('broken', 'Broken'), ('unknown', 'Unknown')],
                default='unknown',
                max_length=20,
            ),
        ),
    ]
