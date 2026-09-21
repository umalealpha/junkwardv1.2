"""Add HealthcareUpload for Revenue / Claims / Treaty trackers.

CFO directive 2026-06-05 — Tlamelo asked for direct omni access for
Health Care bordereaux uploads. This table backs the 3 Smart-Upload
tabs at /healthcare/{revenue,claims,treaty}.
"""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('healthcare', '0002_signing_flow'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HealthcareUpload',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind',       models.CharField(choices=[('revenue','Premium bordereaux (Revenue)'),
                                                          ('claims','AFT claims remit (Claims)'),
                                                          ('treaty','Treaty bordereaux')],
                                                 db_index=True, max_length=12)),
                ('direction',  models.CharField(choices=[('na','n/a'),('inbound','Received from broker'),('outbound','Sent to broker')],
                                                 default='na', max_length=10)),
                ('file_name',  models.CharField(max_length=300)),
                ('file_size',  models.PositiveIntegerField(default=0)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('period_label', models.CharField(blank=True, default='', max_length=64,
                                                   help_text='"May 2026", "2026-W19", etc — derived from the xlsx cover sheet.')),
                ('period_year',  models.PositiveSmallIntegerField(blank=True, null=True)),
                ('period_month', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('total_rows',        models.PositiveIntegerField(default=0)),
                ('total_lives_count', models.PositiveIntegerField(default=0)),
                ('gross_amount',      models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('paid_amount',       models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('status',     models.CharField(choices=[('parsed','Parsed'),('failed','Parse failed')],
                                                 default='parsed', max_length=10)),
                ('error_log',  models.TextField(blank=True, default='')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True,
                                                   on_delete=django.db.models.deletion.SET_NULL,
                                                   related_name='healthcare_uploads',
                                                   to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Healthcare upload',
                'verbose_name_plural': 'Healthcare uploads',
                'ordering': ['-uploaded_at'],
            },
        ),
        migrations.AddIndex(
            model_name='healthcareupload',
            index=models.Index(fields=['kind', '-uploaded_at'], name='healthcare__kind_uplo_idx'),
        ),
    ]
