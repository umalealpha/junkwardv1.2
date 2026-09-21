"""0015 — ELRA monthly performance check-in + PIP (CFO directive 2026-06-25).

Hand-written (the dev env can't run makemigrations); validated by the test-DB
build in CI. Additive only: two new HR tables, no change to any existing model.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0014_leaveopeningbalance'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MonthlyCheckIn',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('period_month', models.PositiveSmallIntegerField()),
                ('period_year', models.PositiveSmallIntegerField()),
                ('conversation_date', models.DateField()),
                ('employment_status', models.CharField(
                    choices=[('PERM', 'Permanent'), ('FIXED', 'Fixed-term'), ('PROB', 'Probation')],
                    default='PERM', max_length=5)),
                ('overall_rating', models.CharField(
                    choices=[('EX', 'Exceeds expectations'), ('ME', 'Meets expectations'),
                             ('PA', 'Partially meets'), ('BE', 'Below expectations'),
                             ('SB', 'Significantly below')], max_length=2)),
                ('objectives', models.JSONField(blank=True, default=list)),
                ('improvement_actions', models.JSONField(blank=True, default=list)),
                ('evidence', models.TextField(blank=True, default='')),
                ('strengths', models.TextField(blank=True, default='')),
                ('concerns', models.TextField(blank=True, default='')),
                ('support_provided', models.TextField(blank=True, default='')),
                ('manager_comments', models.TextField(blank=True, default='')),
                ('employee_response', models.TextField(blank=True, default='')),
                ('employee_ack', models.BooleanField(default=False)),
                ('employee_ack_at', models.DateTimeField(blank=True, null=True)),
                ('recurring_issue', models.BooleanField(default=False)),
                ('consecutive_low_count', models.PositiveSmallIntegerField(default=0)),
                ('warning_recommended', models.BooleanField(default=False)),
                ('pip_triggered', models.BooleanField(default=False)),
                ('referred_to_hr', models.BooleanField(default=False)),
                ('follow_up_date', models.DateField(blank=True, null=True)),
                ('manager_signed_at', models.DateTimeField(blank=True, null=True)),
                ('employee_signed_at', models.DateTimeField(blank=True, null=True)),
                ('is_locked', models.BooleanField(default=False)),
                ('retention_until', models.DateField(blank=True, null=True)),
                ('profile', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='monthly_checkins', to='hris.hrisprofile')),
                ('reviewer', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='hris_checkins_given', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Monthly Performance Check-in',
                'verbose_name_plural': 'Monthly Performance Check-ins',
                'ordering': ['-period_year', '-period_month', 'profile__employee__full_name'],
                'unique_together': {('profile', 'period_month', 'period_year')},
            },
        ),
        migrations.CreateModel(
            name='PerformanceImprovementPlan',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reason', models.TextField(blank=True, default='')),
                ('objectives', models.TextField(blank=True, default='')),
                ('support_plan', models.TextField(blank=True, default='')),
                ('start_date', models.DateField(blank=True, null=True)),
                ('review_date', models.DateField(blank=True, null=True)),
                ('end_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[('open', 'Open'), ('in_progress', 'In progress'), ('met', 'Objectives met'),
                             ('not_met', 'Objectives not met'), ('closed', 'Closed')],
                    default='open', max_length=12)),
                ('outcome', models.TextField(blank=True, default='')),
                ('hr_acknowledged', models.BooleanField(default=False)),
                ('opened_from_checkin', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pips_opened', to='hris.monthlycheckin')),
                ('opened_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='hris_pips_opened', to=settings.AUTH_USER_MODEL)),
                ('profile', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name='pips',
                    to='hris.hrisprofile')),
            ],
            options={
                'verbose_name': 'Performance Improvement Plan',
                'verbose_name_plural': 'Performance Improvement Plans',
                'ordering': ['-start_date', 'profile__employee__full_name'],
            },
        ),
        migrations.AddIndex(
            model_name='monthlycheckin',
            index=models.Index(fields=['profile', 'period_year', 'period_month'],
                               name='hris_checkin_prof_prd_idx'),
        ),
    ]
