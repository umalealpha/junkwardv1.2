"""Initial RealPay monthly report table."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RealPayMonthlyReport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('period_year', models.PositiveSmallIntegerField()),
                ('period_month', models.PositiveSmallIntegerField()),
                ('beneficiary_user_id', models.CharField(max_length=20)),
                ('beneficiary_label', models.CharField(
                    blank=True, default='', max_length=80)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending pull'),
                             ('pulled', 'Pulled, awaiting analysis'),
                             ('analysed', 'Analysed'),
                             ('failed', 'Failed')],
                    default='pending', max_length=12)),
                ('txn_count_total', models.PositiveIntegerField(default=0)),
                ('txn_count_successful', models.PositiveIntegerField(default=0)),
                ('txn_count_failed', models.PositiveIntegerField(default=0)),
                ('amount_collected', models.DecimalField(
                    decimal_places=2, default=0, max_digits=18)),
                ('amount_failed', models.DecimalField(
                    decimal_places=2, default=0, max_digits=18)),
                ('amount_net', models.DecimalField(
                    decimal_places=2, default=0, max_digits=18)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('xlsx_file', models.FileField(
                    blank=True, null=True, upload_to='realpay/%Y/%m/')),
                ('ai_commentary', models.TextField(blank=True, default='')),
                ('pulled_at', models.DateTimeField(blank=True, null=True)),
                ('analysed_at', models.DateTimeField(blank=True, null=True)),
                ('error_log', models.TextField(blank=True, default='')),
                ('pulled_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'RealPay monthly report',
                'verbose_name_plural': 'RealPay monthly reports',
                'ordering': ['-period_year', '-period_month', 'beneficiary_user_id'],
                'abstract': False,
                'unique_together': {('period_year', 'period_month', 'beneficiary_user_id')},
            },
        ),
        migrations.AddIndex(
            model_name='realpaymonthlyreport',
            index=models.Index(fields=['period_year', 'period_month'],
                               name='realpay_period_idx'),
        ),
        migrations.AddIndex(
            model_name='realpaymonthlyreport',
            index=models.Index(fields=['beneficiary_user_id'],
                               name='realpay_user_idx'),
        ),
    ]
