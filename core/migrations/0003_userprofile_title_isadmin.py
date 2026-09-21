# Hand-authored migration — adds title + is_administrator to UserProfile.
# Equivalent to what `manage.py makemigrations core` would produce.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_company'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='title',
            field=models.CharField(
                choices=[
                    ('cfo', 'Chief Financial Officer'),
                    ('finance_manager', 'Finance Manager'),
                    ('financial_controller', 'Financial Controller'),
                    ('accountant', 'Accountant'),
                    ('bookkeeper', 'Bookkeeper'),
                    ('finance_analyst', 'Finance Analyst'),
                    ('auditor', 'Auditor (read-only)'),
                    ('executive', 'Executive (read-only)'),
                    ('operations', 'Operations Staff'),
                    ('system_api', 'System / API'),
                ],
                default='accountant',
                help_text='Job title — drives approval permissions.',
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='is_administrator',
            field=models.BooleanField(
                default=False,
                help_text='If True, this user can grant/revoke roles, titles, '
                          'and admin rights to other users.',
            ),
        ),
    ]
