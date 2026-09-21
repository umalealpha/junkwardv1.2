"""Track the core_auditlog(created_at) perf index.

The index was created live in prod via CREATE INDEX CONCURRENTLY (no lock) on
2026-06-04 to keep the dashboard "last 24h" audit feed fast as the table grows
under transaction volume. This migration backfills it into Django's state +
makes it reproducible on a fresh DB.

Idempotent: the DB operation uses CREATE INDEX IF NOT EXISTS so it no-ops on
prod (already present) and creates it on a clean build. The state operation
keeps AuditLog.Meta.indexes in sync so makemigrations stays quiet.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_intercompany_account_policy'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='auditlog',
                    index=models.Index(
                        fields=['-created_at'],
                        name='auditlog_created_at_idx',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'CREATE INDEX IF NOT EXISTS auditlog_created_at_idx '
                        'ON core_auditlog (created_at DESC);'
                    ),
                    reverse_sql='DROP INDEX IF EXISTS auditlog_created_at_idx;',
                ),
            ],
        ),
    ]
