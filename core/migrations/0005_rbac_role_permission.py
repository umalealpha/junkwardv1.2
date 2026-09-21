# RBAC P1 migration — adds the hierarchical role + permission layer.
#
# Hand-written rather than makemigrations-generated so we can ship without
# the prod-DB round-trip. After applying, `manage.py makemigrations --check`
# should report no further changes for these models. If it does flag
# anything, capture the diff in a follow-up migration.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


DEPARTMENT_CHOICES = [
    ('executive',    'Executive'),
    ('finance',      'Finance'),
    ('claims',       'Claims'),
    ('underwriting', 'Underwriting'),
    ('reinsurance',  'Reinsurance'),
    ('compliance',   'Compliance & Risk'),
    ('hr',           'Human Resources'),
    ('it',           'Information Technology'),
    ('operations',   'Operations'),
    ('external',     'External (auditors / regulators)'),
    ('system',       'System'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_alter_userprofile_title'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # -------------------------------------------------------------- Permission
        migrations.CreateModel(
            name='Permission',
            fields=[
                ('code',        models.CharField(max_length=80, primary_key=True, serialize=False)),
                ('category',    models.CharField(
                                    max_length=40,
                                    help_text='Grouping for UI (ledger, payments, …)',
                                )),
                ('description', models.CharField(max_length=255)),
                ('is_active',   models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['category', 'code'],
            },
        ),

        # -------------------------------------------------------------- Role
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id',          models.UUIDField(
                                    default=uuid.uuid4, editable=False,
                                    primary_key=True, serialize=False,
                                )),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('code',        models.CharField(
                                    max_length=40, unique=True,
                                    help_text='Stable code, e.g. CFO, FINANCE_MANAGER',
                                )),
                ('name',        models.CharField(
                                    max_length=80,
                                    help_text='Human-readable name',
                                )),
                ('description', models.TextField(blank=True)),
                ('level',       models.PositiveSmallIntegerField(
                                    help_text='0 = highest authority (Super Admin); 9 = system accounts',
                                )),
                ('department',  models.CharField(
                                    max_length=20, null=True, blank=True,
                                    choices=DEPARTMENT_CHOICES,
                                    help_text='Natural department; null means cross-department (e.g. Super Admin, CEO)',
                                )),
                ('is_system',   models.BooleanField(
                                    default=False,
                                    help_text='Seed role; protected from deletion',
                                )),
                ('is_active',   models.BooleanField(default=True)),
                ('permissions', models.ManyToManyField(
                                    to='core.permission',
                                    related_name='roles',
                                    blank=True,
                                )),
            ],
            options={
                'ordering': ['level', 'department', 'name'],
            },
        ),

        # -------------------------------------------------------------- UserRoleAssignment
        migrations.CreateModel(
            name='UserRoleAssignment',
            fields=[
                ('id',                models.UUIDField(
                                          default=uuid.uuid4, editable=False,
                                          primary_key=True, serialize=False,
                                      )),
                ('created_at',        models.DateTimeField(auto_now_add=True)),
                ('updated_at',        models.DateTimeField(auto_now=True)),
                ('scope_department',  models.CharField(
                                          max_length=20, null=True, blank=True,
                                          choices=DEPARTMENT_CHOICES,
                                          help_text='If set, overrides role.department for this assignment',
                                      )),
                ('assigned_at',       models.DateTimeField(auto_now_add=True)),
                ('revoked_at',        models.DateTimeField(null=True, blank=True)),
                ('revocation_reason', models.CharField(max_length=255, blank=True)),
                ('justification',     models.CharField(
                                          max_length=500, blank=True,
                                          help_text='Business reason for the grant. REQUIRED for elevated roles (level <= 2).',
                                      )),
                ('expires_at',        models.DateTimeField(
                                          null=True, blank=True,
                                          help_text='If set, assignment becomes inactive after this timestamp '
                                                    '(used for external auditors, contractors, break-glass access).',
                                      )),
                ('notes',             models.CharField(max_length=255, blank=True)),
                ('assigned_by',       models.ForeignKey(
                                          to=settings.AUTH_USER_MODEL,
                                          on_delete=django.db.models.deletion.PROTECT,
                                          related_name='assignments_granted',
                                          null=True, blank=True,
                                      )),
                ('revoked_by',        models.ForeignKey(
                                          to=settings.AUTH_USER_MODEL,
                                          on_delete=django.db.models.deletion.PROTECT,
                                          related_name='assignments_revoked',
                                          null=True, blank=True,
                                      )),
                ('role',              models.ForeignKey(
                                          to='core.role',
                                          on_delete=django.db.models.deletion.PROTECT,
                                          related_name='assignments',
                                      )),
                ('user',              models.ForeignKey(
                                          to=settings.AUTH_USER_MODEL,
                                          on_delete=django.db.models.deletion.CASCADE,
                                          related_name='role_assignments',
                                      )),
            ],
            options={
                'verbose_name': 'User Role Assignment',
                'ordering':     ['-created_at'],
                'indexes': [
                    models.Index(fields=['user', 'revoked_at'], name='core_uras_user_revoked_idx'),
                    models.Index(fields=['role', 'revoked_at'], name='core_uras_role_revoked_idx'),
                ],
            },
        ),
    ]
