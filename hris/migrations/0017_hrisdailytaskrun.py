"""Persist each day's HRIS data-task email so the next day can chase / celebrate
(CFO 2026-06-25 — make the daily email smart)."""
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0016_hrdocument'),
    ]

    operations = [
        migrations.CreateModel(
            name='HRISDailyTaskRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('run_date', models.DateField(db_index=True, unique=True)),
                ('tasks', models.JSONField(blank=True, default=list)),
                ('completeness_pct', models.FloatField(default=0.0)),
                ('focus', models.CharField(blank=True, default='', max_length=200)),
            ],
            options={
                'verbose_name': 'HRIS daily task run',
                'verbose_name_plural': 'HRIS daily task runs',
                'ordering': ['-run_date'],
            },
        ),
    ]
