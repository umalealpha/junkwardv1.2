"""Natural justice for disciplinary cases (CFO directive 2026-08-11).

Adds the invitation-to-respond stage: proof the employee was served the
allegation with a deadline, and the employee's own recorded explanation.
Hand-written to add ONLY these fields — hris/payroll/core carry pre-existing
help_text/index drift that makemigrations would sweep in (see the leave
encashment migration 0043 note).
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # Chains AFTER 0064 rather than forking beside it: the CFO-override
        # migration landed on main in parallel with this branch, and two leaf
        # nodes in the same app make the graph unmigratable.
        ('hris', '0064_disciplinarycase_override_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='disciplinarycase',
            name='inquiry_issued_at',
            field=models.DateTimeField(blank=True, null=True,
                help_text='When the inquiry letter was emailed to the employee.'),
        ),
        migrations.AddField(
            model_name='disciplinarycase',
            name='inquiry_issued_by',
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='disciplinarycase',
            name='inquiry_sent_to',
            field=models.CharField(blank=True, default='', max_length=254,
                help_text='Address the inquiry letter was sent to (evidence of service).'),
        ),
        migrations.AddField(
            model_name='disciplinarycase',
            name='response_deadline',
            field=models.DateField(blank=True, null=True,
                help_text='Date by which the employee must answer.'),
        ),
        migrations.AddField(
            model_name='disciplinarycase',
            name='employee_response',
            field=models.TextField(blank=True, default='',
                help_text="The employee's own explanation, in their words."),
        ),
        migrations.AddField(
            model_name='disciplinarycase',
            name='employee_responded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='disciplinarycase',
            name='response_recorded_by',
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to=settings.AUTH_USER_MODEL,
                help_text='Set only when HR captured a reply received outside omni, '
                          'on the employee behalf.'),
        ),
        migrations.AlterField(
            model_name='disciplinarycase',
            name='status',
            field=models.CharField(db_index=True, default='pending_hr', max_length=16, choices=[
                ('pending_hr', 'Awaiting HR review'),
                ('pending_response', 'Awaiting employee response'),
                ('pending_cfo', 'Awaiting CFO sign-off'),
                ('issued', 'Issued'),
                ('rejected', 'Rejected'),
            ]),
        ),
    ]
