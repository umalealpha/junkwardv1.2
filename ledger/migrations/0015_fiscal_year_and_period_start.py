"""
ledger/migrations/0015_fiscal_year_and_period_start.py

CFO directive 2026-05-20 (post-Manus-TB-audit). Schema foundation for the
Trial Balance architecture rewrite:

  1. New `FiscalYear` model — per-company annual reporting period.
  2. `FiscalPeriod` gains nullable `company` + `fiscal_year` FKs so the same
     calendar month can be tracked separately per legal entity.
  3. `FiscalPeriod.period_name` is no longer globally unique — replaced
     by the composite unique constraint (company, period_name) plus a
     partial UNIQUE INDEX for legacy NULL-company rows.
  4. `JournalEntry.period_start` — nullable DateField so a TB JE can
     declare the start of the period it summarises (entry_date holds
     the end). Reports then compute opening / period / closing buckets
     against the real range instead of inferring from a calendar month.

Data impact at deploy time: prod ledger is currently empty (240k JEs were
wiped 2026-05-20 to prepare for the FY25 + FY26 re-upload), so no JE
backfill is required. FiscalPeriod rows exist for FY25/FY26-onwards
calendar months; their company + fiscal_year FKs stay NULL until the
seed_fiscal_years mgmt cmd back-links them.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('ledger', '0014_frozenfigure'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ---- 1. JournalEntry.period_start ---------------------------------
        migrations.AddField(
            model_name='journalentry',
            name='period_start',
            field=models.DateField(
                blank=True, null=True,
                help_text=(
                    'Start of the period this JE represents. NULL = '
                    'point-in-time JE (period_start = entry_date).'
                ),
            ),
        ),

        # ---- 2. FiscalYear --------------------------------------------------
        migrations.CreateModel(
            name='FiscalYear',
            fields=[
                ('id', models.UUIDField(
                    primary_key=True, serialize=False, editable=False,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('label', models.CharField(
                    max_length=10,
                    help_text='Short label, e.g. "FY25". Unique per company.',
                )),
                ('start_date', models.DateField(
                    help_text='First day of the fiscal year (e.g. 2024-07-01).',
                )),
                ('end_date', models.DateField(
                    help_text='Last day of the fiscal year (e.g. 2025-06-30).',
                )),
                ('status', models.CharField(
                    max_length=10, default='open',
                    choices=[
                        ('open', 'Open'),
                        ('locked', 'Locked'),
                        ('closing', 'Closing'),
                        ('closed', 'Closed'),
                    ],
                )),
                ('locked_at', models.DateTimeField(blank=True, null=True)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('lock_reason', models.TextField(blank=True, default='')),
                ('company', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='fiscal_years',
                    to='core.company',
                )),
                ('locked_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='fiscal_years_locked',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('closed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='fiscal_years_closed',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Fiscal Year',
                'verbose_name_plural': 'Fiscal Years',
                'ordering': ['-end_date', 'company__code'],
            },
        ),
        migrations.AddConstraint(
            model_name='fiscalyear',
            constraint=models.UniqueConstraint(
                fields=('company', 'label'),
                name='uniq_fiscal_year_company_label',
            ),
        ),
        migrations.AddConstraint(
            model_name='fiscalyear',
            constraint=models.UniqueConstraint(
                fields=('company', 'end_date'),
                name='uniq_fiscal_year_company_end_date',
            ),
        ),
        migrations.AddIndex(
            model_name='fiscalyear',
            index=models.Index(
                fields=['company', 'status'],
                name='fiscalyear_company_status_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='fiscalyear',
            index=models.Index(
                fields=['start_date', 'end_date'],
                name='fiscalyear_date_range_idx',
            ),
        ),

        # ---- 3. FiscalPeriod.company + fiscal_year + drop global unique ----
        migrations.AddField(
            model_name='fiscalperiod',
            name='company',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='fiscal_periods',
                to='core.company',
                help_text=(
                    'If set, this period is scoped to a single legal entity. '
                    'NULL = legacy / system-wide (pre-2026-05-20 schema).'
                ),
            ),
        ),
        migrations.AddField(
            model_name='fiscalperiod',
            name='fiscal_year',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='periods',
                to='ledger.fiscalyear',
                help_text=(
                    'Parent fiscal year. Set automatically by '
                    'seed_fiscal_years; NULL on legacy rows.'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='fiscalperiod',
            name='period_name',
            field=models.CharField(max_length=10),
        ),
        migrations.AddConstraint(
            model_name='fiscalperiod',
            constraint=models.UniqueConstraint(
                fields=('company', 'period_name'),
                name='uniq_fiscal_period_company_name',
            ),
        ),
        migrations.AddConstraint(
            model_name='fiscalperiod',
            constraint=models.UniqueConstraint(
                fields=('period_name',),
                condition=models.Q(company__isnull=True),
                name='uniq_fiscal_period_name_when_company_null',
            ),
        ),
    ]
