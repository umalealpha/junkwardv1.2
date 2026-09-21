"""
reporting.0001_reconciliation

First migration in the reporting app. Creates Reconciliation table.
"""
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0002_company'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Reconciliation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('period_label', models.CharField(
                    db_index=True, max_length=24,
                    help_text='FY25, FY26_9M, 2026-03, etc.')),
                ('period_start', models.DateField(blank=True, null=True)),
                ('period_end',   models.DateField(db_index=True)),
                ('metric',       models.CharField(
                    db_index=True, max_length=80,
                    help_text='NEP, GWP, Total Assets, etc.')),
                ('source_a_name',  models.CharField(max_length=80)),
                ('source_a_value', models.DecimalField(decimal_places=2, max_digits=20)),
                ('source_b_name',  models.CharField(max_length=80)),
                ('source_b_value', models.DecimalField(decimal_places=2, max_digits=20)),
                ('delta_bwp',      models.DecimalField(decimal_places=2, max_digits=20)),
                ('delta_pct',      models.DecimalField(decimal_places=4, max_digits=8)),
                ('severity', models.CharField(
                    choices=[('low','Low'),('medium','Medium'),('high','High')],
                    default='low', db_index=True, max_length=8)),
                ('status', models.CharField(
                    choices=[('open','Open'),('explained','Explained'),('resolved','Resolved')],
                    default='open', db_index=True, max_length=10)),
                ('ai_cause',       models.TextField(blank=True, default='')),
                ('ai_fix',         models.TextField(blank=True, default='')),
                ('ai_confidence',  models.DecimalField(
                    blank=True, decimal_places=2, max_digits=4, null=True)),
                ('ai_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('explanation',    models.TextField(
                    blank=True, default='',
                    help_text='Hand-typed sign-off from Finance.')),
                ('detected_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('company', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='reconciliations', to='core.company')),
                ('resolved_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-detected_at'],
                'indexes': [
                    models.Index(fields=['status','severity'],
                                 name='recn_status_sev_idx'),
                    models.Index(fields=['company','period_label','metric'],
                                 name='recn_co_period_metric_idx'),
                ],
                'unique_together': {(
                    'company','period_label','metric',
                    'source_a_name','source_b_name',
                )},
            },
        ),
    ]
