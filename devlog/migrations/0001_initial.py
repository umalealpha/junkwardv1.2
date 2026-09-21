import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """The CFO's build log (CFO 2026-09-09)."""

    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='DevDeploy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sha', models.CharField(max_length=40, unique=True)),
                ('prev_sha', models.CharField(blank=True, default='', max_length=40)),
                ('deployed_at', models.DateTimeField(db_index=True)),
                ('commit_count', models.PositiveIntegerField(default=0)),
                ('ok', models.BooleanField(default=True)),
                ('note', models.CharField(blank=True, default='', max_length=200)),
            ],
            options={'verbose_name': 'Deploy', 'verbose_name_plural': 'Deploys',
                     'ordering': ['-deployed_at'], 'abstract': False},
        ),
        migrations.CreateModel(
            name='DevItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('asked_text', models.TextField()),
                ('title', models.CharField(blank=True, default='', max_length=140)),
                ('asked_at', models.DateTimeField(db_index=True)),
                ('status', models.CharField(choices=[('asked', 'Asked for'), ('building', 'Being built'), ('waiting', 'Built, waiting to go live'), ('live', 'Live'), ('parked', 'Parked'), ('dropped', 'Dropped')], db_index=True, default='asked', max_length=10)),
                ('machine', models.CharField(blank=True, default='', max_length=16)),
                ('source', models.CharField(blank=True, default='', max_length=24)),
                ('session_ref', models.CharField(blank=True, default='', max_length=64)),
                ('area', models.CharField(blank=True, default='', max_length=64)),
                ('success_criteria', models.TextField(blank=True, default='')),
                ('notes', models.TextField(blank=True, default='')),
                ('live_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('client_key', models.CharField(blank=True, db_index=True, default='', max_length=120)),
                ('deploy', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='items', to='devlog.devdeploy')),
            ],
            options={'verbose_name': 'Build item', 'verbose_name_plural': 'Build items',
                     'ordering': ['-asked_at'], 'abstract': False},
        ),
        migrations.CreateModel(
            name='DevCommit',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sha', models.CharField(max_length=40, unique=True)),
                ('author', models.CharField(blank=True, default='', max_length=120)),
                ('subject', models.CharField(blank=True, default='', max_length=300)),
                ('committed_at', models.DateTimeField(db_index=True)),
                ('deploy', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='commits', to='devlog.devdeploy')),
                ('item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commits', to='devlog.devitem')),
            ],
            options={'verbose_name': 'Commit', 'verbose_name_plural': 'Commits',
                     'ordering': ['-committed_at'], 'abstract': False},
        ),
        migrations.AddConstraint(
            model_name='devitem',
            constraint=models.UniqueConstraint(condition=models.Q(('client_key', ''), _negated=True), fields=('client_key',), name='uniq_devitem_client_key'),
        ),
    ]
