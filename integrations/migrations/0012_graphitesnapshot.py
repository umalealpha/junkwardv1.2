"""The letterbox for Graphite's analytics snapshots (Appendix A v1)."""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('integrations', '0011_snapshot_reported_no_track')]

    operations = [
        migrations.CreateModel(
            name='GraphiteSnapshot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('dataset', models.CharField(
                    db_index=True, max_length=64, unique=True,
                    help_text='Stable key for the tab, e.g. weekly_update, major_claims.')),
                ('payload', models.JSONField(default=dict)),
                ('row_count', models.PositiveIntegerField(default=0)),
                ('content_hash', models.CharField(
                    blank=True, default='', max_length=64,
                    help_text='SHA-256 of the body, so a re-sent identical snapshot is '
                              'recognised instead of counted as fresh data.')),
                ('received_at', models.DateTimeField(auto_now=True)),
                ('source_ip', models.CharField(blank=True, default='', max_length=45)),
            ],
            options={
                'verbose_name': 'Graphite snapshot',
                'ordering': ['dataset'],
                'abstract': False,
            },
        ),
    ]
