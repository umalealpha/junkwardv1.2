"""Add hide_in_banking_ui flag to BankAccount (CFO directive 2026-05-25).

FM wants a per-account show/hide toggle on the FNB Integration page
without deactivating accounts everywhere else in the system.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0002_bankrecrule'),
    ]

    operations = [
        migrations.AddField(
            model_name='bankaccount',
            name='hide_in_banking_ui',
            field=models.BooleanField(default=False),
        ),
    ]
