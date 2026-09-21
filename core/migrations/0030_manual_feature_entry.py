import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_helpdesk_comment'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManualFeatureEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('commit_sha', models.CharField(db_index=True, max_length=40, unique=True)),
                ('commit_date', models.DateField(db_index=True)),
                ('title', models.CharField(max_length=200)),
                ('summary', models.TextField(blank=True, default='')),
                ('area', models.CharField(blank=True, default='', max_length=60)),
                ('raw_subject', models.CharField(blank=True, default='', max_length=300)),
                ('published', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Manual feature entry',
                'verbose_name_plural': 'Manual feature entries',
                'ordering': ['-commit_date', '-created_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ManualUpdateRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('run_date', models.DateField(db_index=True, unique=True)),
                ('commits_seen', models.IntegerField(default=0)),
                ('entries_added', models.IntegerField(default=0)),
                ('last_commit_sha', models.CharField(blank=True, default='', max_length=40)),
                ('notes', models.TextField(blank=True, default='')),
            ],
            options={
                'verbose_name': 'Manual update run',
                'verbose_name_plural': 'Manual update runs',
                'ordering': ['-run_date'],
                'abstract': False,
            },
        ),
    ]
