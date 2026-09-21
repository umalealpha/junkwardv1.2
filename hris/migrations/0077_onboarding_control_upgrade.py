# Generated for the Omni onboarding maker-checker control upgrade.

import django.db.models.deletion
import django.db.models.functions.text
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0076_monthlycheckin_auto_posted_and_more'),
        ('payroll', '0021_employee_archive'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='hrisprofile',
            name='default_leave_approver',
            field=models.ForeignKey(
                blank=True,
                help_text='Preferred leave reviewer for this employee. Falls back to the line manager.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='default_leave_approvals',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name='OnboardingRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('full_name', models.CharField(max_length=200)),
                ('email', models.EmailField(db_index=True, max_length=254)),
                ('employee_number', models.CharField(blank=True, default='', max_length=30)),
                ('department', models.CharField(blank=True, default='', max_length=100)),
                ('job_title', models.CharField(blank=True, default='', max_length=100)),
                ('phone', models.CharField(blank=True, default='', max_length=50)),
                ('hire_date', models.DateField()),
                ('status', models.CharField(choices=[('pending', 'Pending approval'), ('approved', 'Approved and applied'), ('rejected', 'Rejected')], db_index=True, default='pending', max_length=10)),
                ('risk_warnings', models.JSONField(blank=True, default=list)),
                ('payload_hash', models.CharField(db_index=True, max_length=64)),
                ('correlation_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('maker_email', models.EmailField(blank=True, default='', max_length=254)),
                ('approver_email', models.EmailField(blank=True, default='', max_length=254)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_notes', models.TextField(blank=True, default='')),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='onboarding_requests_decided', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='onboarding_requests', to='core.company')),
                ('default_leave_approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='onboarding_requests_as_leave_approver', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approved_onboarding_requests', to='payroll.employee')),
                ('maker', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='onboarding_requests_made', to=settings.AUTH_USER_MODEL)),
                ('manager', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='onboarding_requests_as_manager', to='payroll.employee')),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
                'indexes': [
                    models.Index(fields=['status', '-created_at'], name='hris_onbrd_status_created_idx'),
                    models.Index(fields=['email', 'status'], name='hris_onbrd_email_status_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower('email'),
                        condition=models.Q(('status', 'pending')),
                        name='uniq_pending_onboarding_email_ci',
                    ),
                ],
            },
        ),
    ]
