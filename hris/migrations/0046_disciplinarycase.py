# Hand-trimmed to ONLY the new DisciplinaryCase table (CFO directive 2026-07-22).
# makemigrations also emits unrelated help_text/index drift on core/payroll/hris
# that pre-exists on main; that drift is deliberately excluded (surgical-change rule).

import core.models
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0044_datasubjectrequest'),
        ('hris', '0045_hris_excite_features'),
        ('payroll', '0012_encrypt_employee_pii'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DisciplinaryCase',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('subject_name', models.CharField(blank=True, default='', help_text='Snapshot of the employee name at raise time.', max_length=191)),
                ('raised_by_email', models.CharField(blank=True, default='', max_length=254)),
                ('category', models.CharField(choices=[('verbal', 'Verbal warning'), ('written', 'Written warning'), ('final', 'Final written warning'), ('suspension', 'Suspension'), ('dismissal', 'Dismissal recommendation')], db_index=True, max_length=16)),
                ('incident_date', models.DateField(help_text='When the incident occurred.')),
                ('allegation', models.TextField(help_text='Factual description of the misconduct (minimum 50 words).')),
                ('proposed_action', models.TextField(blank=True, default='', help_text='What the manager proposes (e.g. written warning on file).')),
                ('status', models.CharField(choices=[('pending_hr', 'Awaiting HR review'), ('pending_cfo', 'Awaiting CFO sign-off'), ('issued', 'Issued'), ('rejected', 'Rejected')], db_index=True, default='pending_hr', max_length=16)),
                ('hr_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('cfo_approved_at', models.DateTimeField(blank=True, null=True)),
                ('issued_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_stage', models.CharField(blank=True, default='', max_length=8)),
                ('decision_notes', models.TextField(blank=True, default='')),
                ('cfo_approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('hr_reviewer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('raised_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='disciplinary_raised', to=settings.AUTH_USER_MODEL)),
                ('rejected_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('subject_employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='disciplinary_cases', to='payroll.employee')),
            ],
            options={
                'verbose_name': 'Disciplinary Case',
                'verbose_name_plural': 'Disciplinary Cases',
                'ordering': ['-created_at'],
                'abstract': False,
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
    ]
