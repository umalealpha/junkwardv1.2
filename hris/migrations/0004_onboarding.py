"""
0004_onboarding — OnboardingTask model (30-60-90 + new-hire journey).

CFO directive 2026-05-20 (Manus HRIS audit Part 4 — wow-factor extensions).
"""

import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0003_recognition'),
        ('payroll', '0004_payrollperiod_status_posted_journal_entry'),
    ]

    operations = [
        migrations.CreateModel(
            name='OnboardingTask',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title',      models.CharField(max_length=160)),
                ('category',   models.CharField(
                    max_length=12,
                    default='other',
                    choices=[
                        ('it',         'IT account setup'),
                        ('access',     'Building / system access'),
                        ('training',   'Training / orientation'),
                        ('paperwork',  'Paperwork / signatures'),
                        ('buddy',      'Buddy / introductions'),
                        ('check30',    '30-day manager check-in'),
                        ('check60',    '60-day manager check-in'),
                        ('check90',    '90-day manager check-in'),
                        ('other',      'Other'),
                    ],
                )),
                ('due_date',   models.DateField(null=True, blank=True)),
                ('status',     models.CharField(
                    max_length=10,
                    default='open',
                    choices=[
                        ('open',    'Open'),
                        ('done',    'Done'),
                        ('skipped', 'Skipped'),
                    ],
                )),
                ('notes',      models.TextField(blank=True, default='')),
                ('employee',   models.ForeignKey(
                    on_delete=models.CASCADE,
                    related_name='onboarding_tasks',
                    to='payroll.employee',
                )),
                ('owner_user', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.SET_NULL,
                    related_name='owned_onboarding_tasks',
                    to=settings.AUTH_USER_MODEL,
                    help_text='Who needs to action this task.',
                )),
            ],
            options={
                'verbose_name':        'Onboarding Task',
                'verbose_name_plural': 'Onboarding Tasks',
                'ordering':            ['due_date', 'created_at'],
            },
        ),
    ]
