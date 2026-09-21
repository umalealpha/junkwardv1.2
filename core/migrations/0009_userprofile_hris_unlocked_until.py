"""UserProfile.hris_unlocked_until — second-factor lock for HRIS."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_company_entity_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='hris_unlocked_until',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='HRIS unlock expiry. NULL = locked.',
            ),
        ),
    ]
