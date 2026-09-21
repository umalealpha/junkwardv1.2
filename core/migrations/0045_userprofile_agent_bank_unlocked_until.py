# Agent-portal bank-number reveal gate (Motlatsi Molefe 2026-07-22): a per-user
# reveal-unlock expiry so bank account numbers stay masked until the reveal
# password is entered. ONLY the new UserProfile field is touched here.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0044_datasubjectrequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='agent_bank_unlocked_until',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Agent-portal bank-number reveal expiry. NULL = masked.'),
        ),
    ]
