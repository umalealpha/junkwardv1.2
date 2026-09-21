"""Row-count watchdog storage (CFO 2026-07-31).

Purely ADDITIVE — one new table, nothing altered or removed. That matters because
deploys now run blue/green: for ~66s the old and new backend both talk to this same
database, so a migration that dropped or renamed anything would break the old
version while it is still serving. Add now, remove later, never both at once.
"""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0054_mailautoreply'),
    ]

    operations = [
        migrations.CreateModel(
            name='RowCountSnapshot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('taken_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                # {"app_label.ModelName": row_count} for every countable table.
                ('counts', models.JSONField(default=dict)),
                ('table_count', models.PositiveIntegerField(default=0)),
                ('total_rows', models.BigIntegerField(default=0)),
                ('alerted', models.BooleanField(default=False)),
                ('note', models.TextField(blank=True, default='')),
            ],
            options={
                'ordering': ['-taken_at'],
                'verbose_name': 'row count snapshot',
            },
        ),
    ]
