"""
ledger.0017_account_merge_audit

CFO directive 2026-05-25 (COA-001). One row per "we merged this
duplicate-name account into a keeper" decision. Used by the
merge_duplicate_accounts management command as its audit trail.
Never deleted — auditors will want this history.
"""
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0016_account_is_receivable'),
        ('core',   '0002_company'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountMergeAudit',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('account_name',   models.CharField(max_length=200, db_index=True)),
                ('keeper_id',      models.UUIDField(db_index=True)),
                ('keeper_code',    models.CharField(max_length=20)),
                ('merged_id',      models.UUIDField(db_index=True)),
                ('merged_code',    models.CharField(max_length=20)),
                ('je_lines_moved', models.IntegerField(default=0)),
                ('rule_applied',   models.CharField(max_length=80)),
                ('company', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='account_merge_audits', to='core.company')),
                ('performed_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL)),
                ('performed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('notes',        models.TextField(blank=True, default='')),
            ],
            options={
                'ordering': ['-performed_at'],
                'indexes': [
                    models.Index(fields=['company', 'account_name'],
                                 name='ama_co_name_idx'),
                ],
            },
        ),
    ]
