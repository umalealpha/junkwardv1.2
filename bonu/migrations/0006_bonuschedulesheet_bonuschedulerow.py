# Hand-written to match bonu/models.py (no container engine on the build machine).
# MUST be checked with `makemigrations bonu --check --dry-run` before deploy — expect
# "No changes detected".

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bonu', '0005_membertokensalt_bonuinvoiceline_member_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='BonuScheduleSheet',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.SlugField(max_length=40, unique=True)),
                ('title', models.CharField(max_length=80)),
                ('columns', models.JSONField(default=list)),
                ('amount_column', models.CharField(blank=True, default='', max_length=80)),
                ('source_note', models.CharField(blank=True, default='', max_length=200)),
                ('order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'BONU schedule sheet',
                'ordering': ['order', 'title'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='BonuScheduleRow',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('position', models.PositiveIntegerField(default=0)),
                ('cells', models.JSONField(default=dict)),
                ('note', models.TextField(blank=True, default='')),
                ('updated_by_email', models.CharField(blank=True, default='', max_length=200)),
                ('sheet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rows', to='bonu.bonuschedulesheet')),
            ],
            options={
                'verbose_name': 'BONU schedule row',
                'ordering': ['sheet', 'position', 'created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='bonuschedulerow',
            index=models.Index(fields=['sheet', 'position'], name='bonu_schedrow_pos_idx'),
        ),
    ]
