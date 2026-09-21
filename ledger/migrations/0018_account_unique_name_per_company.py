"""
ledger.0018_account_unique_name_per_company

CFO directive 2026-05-25 (COA-001 final step). After
merge_duplicate_accounts has run, enforce one name per company
forever. The constraint is restricted to is_active=True rows so the
deactivated history we just preserved doesn't collide with the
active keeper.

Migration fails loudly if any duplicate still exists.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0017_account_merge_audit'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='account',
            constraint=models.UniqueConstraint(
                fields=['owner_company', 'name'],
                condition=models.Q(is_active=True),
                name='uniq_active_account_name_per_company',
            ),
        ),
    ]
