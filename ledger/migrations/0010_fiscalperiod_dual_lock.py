"""Dual-protection lock on FiscalPeriod.

Adds LOCKED status + four signature fields (CFO + FM, each with user FK and
timestamp) + a lock_reason text. Migration is forward-only safe: existing
periods retain status='open' and null signatures. CFO directive 2026-05-14.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0009_alter_recurringjournalentry_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='fiscalperiod',
            name='status',
            field=models.CharField(
                choices=[
                    ('open',    'Open'),
                    ('locked',  'Locked'),
                    ('closing', 'Closing'),
                    ('closed',  'Closed'),
                ],
                default='open',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='fiscalperiod',
            name='locked_by_cfo',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='periods_signed_cfo',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='fiscalperiod',
            name='locked_by_cfo_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fiscalperiod',
            name='locked_by_fm',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='periods_signed_fm',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='fiscalperiod',
            name='locked_by_fm_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='fiscalperiod',
            name='lock_reason',
            field=models.TextField(blank=True, default=''),
        ),
    ]
