from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the per-user 'assignee_home_department' override for the task
    assignee picker (CFO 2026-08-31). Hand-written to add ONLY this field and
    avoid the pre-existing help_text/index drift a full makemigrations emits
    across core/payroll/hris."""

    dependencies = [
        ('core', '0062_bugreport_manus_qc'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='assignee_home_department',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Department to show first in this user's task assignee "
                          "picker; blank = their own department.",
                max_length=100,
            ),
        ),
    ]
