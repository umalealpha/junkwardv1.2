"""
0001_initial — NBFIRA quarterly + audit + capital factor models.
CFO directive 2026-05-22 (NBFIRA_MODULE_BLUEPRINT.md).
"""

import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NBFIRAReturn',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('type',         models.CharField(max_length=12, default='quarterly',
                    choices=[('quarterly', 'Quarterly (Short-term Insurer)'),
                             ('annual',    'Annual (IMF General Insurance)')])),
                ('period_label', models.CharField(max_length=20)),
                ('period_start', models.DateField()),
                ('period_end',   models.DateField()),
                ('status', models.CharField(max_length=12, default='draft', choices=[
                    ('draft','Draft'),('reviewed','Reviewed'),('approved','Approved'),
                    ('locked','Locked (immutable)'),('submitted','Submitted to NBFIRA'),
                    ('rejected','Rejected'),('reopened','Reopened'),
                ])),
                ('initiated_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at',  models.DateTimeField(null=True, blank=True)),
                ('approved_at',  models.DateTimeField(null=True, blank=True)),
                ('locked_at',    models.DateTimeField(null=True, blank=True)),
                ('submitted_at', models.DateTimeField(null=True, blank=True)),
                ('notes',        models.TextField(blank=True, default='')),
                ('company', models.ForeignKey(null=True, blank=True, on_delete=models.PROTECT,
                    related_name='nbfira_returns', to='core.company')),
                ('initiated_by', models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL,
                    related_name='nbfira_returns_initiated', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL,
                    related_name='nbfira_returns_reviewed', to=settings.AUTH_USER_MODEL)),
                ('approved_by', models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL,
                    related_name='nbfira_returns_approved', to=settings.AUTH_USER_MODEL)),
                ('locked_by', models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL,
                    related_name='nbfira_returns_locked', to=settings.AUTH_USER_MODEL)),
                ('submitted_by', models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL,
                    related_name='nbfira_returns_submitted', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'NBFIRA Return',
                'verbose_name_plural': 'NBFIRA Returns',
                'ordering': ['-period_end'],
                'unique_together': {('type', 'period_label', 'company')},
            },
        ),
        migrations.CreateModel(
            name='NBFIRAReturnLine',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('schedule',   models.CharField(max_length=8)),
                ('section',    models.CharField(max_length=40, blank=True, default='')),
                ('line_code',  models.CharField(max_length=40)),
                ('label',      models.CharField(max_length=200)),
                ('value',      models.DecimalField(max_digits=20, decimal_places=4, default=0)),
                ('source_accounts', models.JSONField(default=list, blank=True)),
                ('formula',    models.CharField(max_length=200, blank=True, default='')),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('return_obj', models.ForeignKey(on_delete=models.CASCADE,
                    related_name='lines', to='nbfira.nbfirareturn')),
            ],
            options={
                'verbose_name': 'NBFIRA Return Line',
                'verbose_name_plural': 'NBFIRA Return Lines',
                'ordering': ['return_obj', 'schedule', 'sort_order', 'line_code'],
                'unique_together': {('return_obj', 'schedule', 'line_code')},
            },
        ),
        migrations.AddIndex(
            model_name='nbfirareturnline',
            index=models.Index(fields=['return_obj', 'schedule'], name='nbf_line_ret_sch_idx'),
        ),
        migrations.CreateModel(
            name='NBFIRACapitalFactor',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(max_length=4, choices=[
                    ('irc','Insurance Risk Capital factor'),
                    ('mrc','Market Risk Capital factor'),
                    ('g',  'g-factor (Insurance / Market)'),
                    ('mcr','Minimum Capital Requirement (BWP)'),
                ])),
                ('key',           models.CharField(max_length=60)),
                ('factor_value',  models.DecimalField(max_digits=14, decimal_places=4)),
                ('effective_from', models.DateField()),
                ('effective_to',   models.DateField(null=True, blank=True)),
                ('notes',          models.TextField(blank=True, default='')),
            ],
            options={
                'verbose_name': 'NBFIRA Capital Factor',
                'verbose_name_plural': 'NBFIRA Capital Factors',
                'ordering': ['kind', 'key', '-effective_from'],
                'unique_together': {('kind', 'key', 'effective_from')},
            },
        ),
        migrations.CreateModel(
            name='NBFIRASubmission',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('submitted_at',        models.DateTimeField(auto_now_add=True)),
                ('file_name',           models.CharField(max_length=200, blank=True, default='')),
                ('file_hash_sha256',    models.CharField(max_length=64, blank=True, default='')),
                ('filing_reference',    models.CharField(max_length=80, blank=True, default='')),
                ('acknowledgement_ref', models.CharField(max_length=80, blank=True, default='')),
                ('proof_of_submission', models.FileField(upload_to='nbfira/proofs/', null=True, blank=True)),
                ('return_obj', models.OneToOneField(on_delete=models.PROTECT,
                    related_name='submission', to='nbfira.nbfirareturn')),
                ('submitted_by', models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL,
                    related_name='nbfira_submissions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'NBFIRA Submission',
                'verbose_name_plural': 'NBFIRA Submissions',
                'ordering': ['-submitted_at'],
            },
        ),
        migrations.CreateModel(
            name='NBFIRAAuditLog',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(max_length=12, choices=[
                    ('create','Create draft'),('generate','Generate schedules from GL'),
                    ('edit','Edit a line value'),('review','Mark as reviewed'),
                    ('approve','Approve'),('reject','Reject'),('reopen','Reopen'),
                    ('lock','Lock'),('unlock','Unlock (privileged)'),
                    ('export','Export XLSX/PDF'),('submit','Mark as submitted'),
                ])),
                ('comment',     models.TextField(blank=True, default='')),
                ('before_json', models.JSONField(default=dict, blank=True)),
                ('after_json',  models.JSONField(default=dict, blank=True)),
                ('ip_address',  models.CharField(max_length=45, blank=True, default='')),
                ('timestamp',   models.DateTimeField(auto_now_add=True)),
                ('file_hash',   models.CharField(max_length=64, blank=True, default='')),
                ('return_obj', models.ForeignKey(on_delete=models.PROTECT,
                    related_name='audit_logs', to='nbfira.nbfirareturn')),
                ('user', models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL,
                    related_name='nbfira_audit_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'NBFIRA Audit Log',
                'verbose_name_plural': 'NBFIRA Audit Log',
                'ordering': ['-timestamp'],
            },
        ),
        migrations.AddIndex(
            model_name='nbfiraauditlog',
            index=models.Index(fields=['return_obj', 'timestamp'], name='nbf_audit_ret_ts_idx'),
        ),
    ]
