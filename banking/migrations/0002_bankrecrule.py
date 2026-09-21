# Generated for Banking + Treasury upgrade: BankRecRule engine.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0001_initial'),
        ('core', '0001_initial'),
        ('ledger', '0016_account_is_receivable'),
        ('billing', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BankRecRule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('priority', models.IntegerField(
                    default=100,
                    help_text='Lower number = higher priority. First match wins.',
                )),
                ('description_regex', models.CharField(
                    max_length=500,
                    help_text='Case-insensitive Python regex matched against '
                              'BankStatementLine.description.',
                )),
                ('amount_min', models.DecimalField(
                    max_digits=18, decimal_places=2, null=True, blank=True,
                    help_text='Inclusive minimum signed amount. NULL = no lower bound.',
                )),
                ('amount_max', models.DecimalField(
                    max_digits=18, decimal_places=2, null=True, blank=True,
                    help_text='Inclusive maximum signed amount. NULL = no upper bound.',
                )),
                ('action', models.CharField(
                    max_length=10,
                    choices=[('auto_je', 'Auto-post draft JE'),
                             ('propose', 'Propose only (no JE)')],
                    default='auto_je',
                )),
                ('is_active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='bank_rec_rules', to='core.company',
                )),
                ('target_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='bank_rec_rules', to='ledger.account',
                    help_text='Counter-leg GL account used when a draft JE is built.',
                )),
                ('target_contact', models.ForeignKey(
                    on_delete=django.db.models.deletion.SET_NULL,
                    null=True, blank=True,
                    related_name='bank_rec_rules', to='billing.contact',
                    help_text='Optional contact tagged on the JE line.',
                )),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='bank_rec_rules_created',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['priority', 'created_at'],
                'verbose_name': 'Bank Reconciliation Rule',
                'verbose_name_plural': 'Bank Reconciliation Rules',
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='bankrecrule',
            index=models.Index(
                fields=['company', 'is_active', 'priority'],
                name='banking_ban_company_e6e8b3_idx',
            ),
        ),
    ]
