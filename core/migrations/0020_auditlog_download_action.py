"""Add 'download' to AuditLog.Action choices.

Expert-audit fix 2026-06-05 (PwC reviewer): the renewal-pack download view
logs action='download', which was not a valid Action choice, so the audit
trail could not be relied on. Choices are advisory at the DB level (no CHECK
constraint), so this AlterField is a state-only sync — it makes 'download' a
first-class, reportable audit action.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_auditlog_created_at_index'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(
                max_length=10,
                choices=[
                    ('create', 'Create'),
                    ('update', 'Update'),
                    ('delete', 'Delete'),
                    ('post', 'Post'),
                    ('reverse', 'Reverse'),
                    ('approve', 'Approve'),
                    ('download', 'Download'),
                ],
            ),
        ),
    ]
