# Generated for Banking + Treasury upgrade: FxRevaluationRun.

import django.db.models.deletion
import uuid
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fx', '0001_initial'),
        ('core', '0001_initial'),
        ('ledger', '0016_account_is_receivable'),
    ]

    operations = [
        migrations.CreateModel(
            name='FxRevaluationRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('period_end', models.DateField()),
                ('total_delta_bwp', models.DecimalField(
                    max_digits=18, decimal_places=2, default=Decimal('0.00'),
                    help_text='Sum of all unrealised FX deltas posted in '
                              'BWP. Positive = gain, negative = loss.',
                )),
                ('is_reversed', models.BooleanField(
                    default=False,
                    help_text='Flip to True when journal_entry is reversed '
                              'so the same (company, period_end) becomes '
                              'eligible for a re-run.',
                )),
                ('company', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='fx_revaluation_runs', to='core.company',
                )),
                ('journal_entry', models.ForeignKey(
                    on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                    related_name='fx_revaluation_runs',
                    to='ledger.journalentry',
                )),
            ],
            options={
                'verbose_name': 'FX Revaluation Run',
                'verbose_name_plural': 'FX Revaluation Runs',
                'ordering': ['-period_end', '-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='fxrevaluationrun',
            index=models.Index(
                fields=['company', 'period_end', 'is_reversed'],
                name='fx_fxrevalu_company_8a1c1f_idx',
            ),
        ),
    ]
