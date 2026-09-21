"""Staff Incentive Approval workflow (CFO directive 2026-07-13).

IncentiveRequest (dual CFO + HR signatures, payroll-processed tick) +
IncentiveLine. No seed data — requests are created by managers in the UI.
"""
from __future__ import annotations

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0024_career_track'),
        ('payroll', '0010_employmentcontract_type_probation'),
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='IncentiveRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=160)),
                ('period', models.CharField(
                    help_text='Incentive month, format YYYY-MM.',
                    max_length=7)),
                ('department', models.CharField(
                    blank=True, default='', max_length=80)),
                ('notes', models.TextField(blank=True, default='')),
                ('maker_email', models.CharField(
                    blank=True, default='', max_length=254)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending approval'),
                             ('approved', 'Approved'),
                             ('rejected', 'Rejected')],
                    db_index=True, default='pending', max_length=12)),
                ('cfo_approved_at', models.DateTimeField(blank=True, null=True)),
                ('hr_approved_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_at', models.DateTimeField(blank=True, null=True)),
                ('decision_notes', models.TextField(blank=True, default='')),
                ('payroll_processed', models.BooleanField(default=False)),
                ('payroll_processed_at', models.DateTimeField(blank=True, null=True)),
                ('cfo_approver', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL)),
                ('hr_approver', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL)),
                ('rejected_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL)),
                ('payroll_processed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL)),
                ('maker', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='incentive_requests',
                    to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='incentive_requests', to='core.company')),
            ],
            options={
                'verbose_name': 'Incentive Request',
                'verbose_name_plural': 'Incentive Requests',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='IncentiveLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(
                    help_text='Person or category, e.g. "BW instant '
                              'insurance incentive" or an employee name.',
                    max_length=160)),
                ('basis', models.CharField(
                    blank=True, default='', max_length=200)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='incentive_lines', to='payroll.employee')),
                ('request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='lines', to='hris.incentiverequest')),
            ],
            options={
                'ordering': ['created_at'],
                'abstract': False,
            },
        ),
    ]
