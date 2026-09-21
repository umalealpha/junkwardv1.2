"""Stamp a case issued without the employee being heard (CFO decision 2026-08-12).

The CFO may force past the natural-justice gate on cfo_override, but only with a
recorded reason that stays on the file permanently. Hand-written to add only these
two fields — makemigrations would sweep in the pre-existing help_text/index drift
in hris/payroll/core.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hris', '0065_disciplinary_natural_justice'),
    ]

    operations = [
        migrations.AddField(
            model_name='disciplinarycase',
            name='unheard_issue_reason',
            field=models.TextField(blank=True, default='',
                help_text='Reason given for issuing without the employee being heard. '
                          'Set only when the natural-justice gate was forced.'),
        ),
        migrations.AddField(
            model_name='disciplinarycase',
            name='unheard_issued_by',
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to=settings.AUTH_USER_MODEL),
        ),
    ]
