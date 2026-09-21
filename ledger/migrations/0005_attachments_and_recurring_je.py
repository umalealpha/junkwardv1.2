# Hand-authored migration:
#   - JournalEntryAttachment   (supporting documents on JEs)
#   - RecurringJournalEntry    (template)
#   - RecurringJournalEntryLine (template lines)

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import ledger.models  # for _je_attachment_path callable


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
        ('core', '0003_userprofile_title_isadmin'),
        ('ledger', '0004_je_approval_workflow'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='JournalEntryAttachment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(upload_to=ledger.models._je_attachment_path)),
                ('filename', models.CharField(help_text='Original filename as uploaded.', max_length=255)),
                ('file_size_bytes', models.PositiveBigIntegerField(default=0)),
                ('content_type', models.CharField(blank=True, default='', max_length=100)),
                ('description', models.CharField(blank=True, default='', max_length=500)),
                ('journal_entry', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='attachments',
                    to='ledger.journalentry',
                )),
                ('uploaded_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='je_attachments_uploaded',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Journal Entry Attachment',
                'verbose_name_plural': 'Journal Entry Attachments',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RecurringJournalEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(help_text='e.g. "Monthly office rent accrual"', max_length=200)),
                ('description', models.CharField(max_length=500)),
                ('journal_type', models.CharField(
                    choices=[
                        ('sales', 'Sales'), ('purchases', 'Purchases'),
                        ('cash_receipts', 'Cash Receipts'), ('cash_payments', 'Cash Payments'),
                        ('bank', 'Bank'), ('general', 'General'),
                    ],
                    default='general', max_length=15,
                )),
                ('frequency', models.CharField(
                    choices=[
                        ('monthly', 'Monthly'),
                        ('quarterly', 'Quarterly'),
                        ('semiannual', 'Semi-annual'),
                        ('annual', 'Annual'),
                    ],
                    default='monthly', max_length=12,
                )),
                ('day_of_period', models.PositiveSmallIntegerField(default=1)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField(blank=True, null=True)),
                ('last_generated_for', models.DateField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='recurring_journal_entries',
                    to='core.company',
                )),
                ('currency_code', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='recurring_journal_entries',
                    to='core.currency',
                )),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='recurring_journal_entries_created',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Recurring Journal Entry',
                'verbose_name_plural': 'Recurring Journal Entries',
                'ordering': ['name'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RecurringJournalEntryLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('description', models.CharField(blank=True, default='', max_length=500)),
                ('debit_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('credit_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='recurring_je_lines',
                    to='ledger.account',
                )),
                ('contact', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='recurring_je_lines',
                    to='billing.contact',
                )),
                ('template', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='lines',
                    to='ledger.recurringjournalentry',
                )),
            ],
            options={
                'verbose_name': 'Recurring JE Line',
                'verbose_name_plural': 'Recurring JE Lines',
                'ordering': ['created_at'],
                'abstract': False,
            },
        ),
    ]
