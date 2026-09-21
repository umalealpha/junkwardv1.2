"""Morning Bank Balances (CFO 2026-09-20).

Three schema changes, all of them in service of one rule: a figure we do not
have must never be shown as a figure we do.

1.  ``BankAccount.fnb_balance_watch`` — which accounts the FNB jobs read, by a
    FLAG. The old ``bank_name/account_name icontains 'FNB'`` match missed
    Veritas, Risk Software and Unicoin entirely (their bank_name is "First
    National Bank Botswana", which does not contain "FNB") and pulled in the
    credit card control account, which fails with HTTP 400 every morning.
2.  ``BankStatement.opening_balance`` / ``closing_balance`` become NULLABLE.
    "The bank sent no balance block" was being stored as 0.00, so the Claims
    account reported a confident P0.00 while holding about P256,000.
    Existing rows are NOT touched: they keep the 0.00 they were written with,
    because a historical 0.00 can no longer be told apart from a historical
    unknown, and a migration does not get to rewrite live financial rows.
3.  ``BankBalanceSnapshot`` — one row per watched account per pull. Balances
    stay off BankStatement on purpose; see the model docstring.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0008_bankmatchmemory'),
    ]

    operations = [
        migrations.AddField(
            model_name='bankaccount',
            name='fnb_balance_watch',
            field=models.BooleanField(
                default=False,
                help_text='Read this account from FNB — daily statements and '
                          'the morning balance pulls. Set deliberately, never '
                          'inferred from the account name.',
            ),
        ),
        migrations.AddField(
            model_name='bankaccount',
            name='fnb_statement_pull',
            field=models.BooleanField(
                default=False,
                help_text="Pull this account's daily statement from FNB. Set "
                          'deliberately, never inferred from the account name.',
            ),
        ),
        migrations.AlterField(
            model_name='bankstatement',
            name='opening_balance',
            field=models.DecimalField(max_digits=18, decimal_places=2,
                                      null=True, blank=True),
        ),
        migrations.AlterField(
            model_name='bankstatement',
            name='closing_balance',
            field=models.DecimalField(max_digits=18, decimal_places=2,
                                      null=True, blank=True),
        ),
        migrations.CreateModel(
            name='BankBalanceSnapshot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('taken_at', models.DateTimeField(db_index=True)),
                ('as_of_date', models.DateField()),
                ('opening', models.DecimalField(max_digits=18, decimal_places=2,
                                                null=True, blank=True)),
                ('closing', models.DecimalField(max_digits=18, decimal_places=2,
                                                null=True, blank=True)),
                ('outcome', models.CharField(max_length=24, choices=[
                    ('ok', 'Balance read'),
                    ('no_balance_returned', 'Bank answered, no balance block'),
                    ('failed', 'The read failed'),
                ])),
                ('error_text', models.TextField(blank=True, default='')),
                ('source', models.CharField(max_length=10, default='scheduled',
                                            choices=[('scheduled', 'Scheduled pull'),
                                                     ('manual', 'Run by hand')])),
                ('bank_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='balance_snapshots', to='banking.bankaccount')),
            ],
            options={'ordering': ['-taken_at']},
        ),
        migrations.AddIndex(
            model_name='bankbalancesnapshot',
            index=models.Index(fields=['bank_account', '-taken_at'],
                               name='bankbal_acct_taken_idx'),
        ),
    ]
