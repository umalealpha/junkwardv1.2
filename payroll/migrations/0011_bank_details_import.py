# Hand-authored (house style) — bank-details bulk importer.
# Adds Employee.bank_branch_code (numeric BW sort code, drives bank-name
# derivation) + the BankDetailImportBatch approval batch (CFO 2026-07-15).

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0010_employmentcontract_type_probation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='bank_branch_code',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.CreateModel(
            name='BankDetailImportBatch',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file_name', models.CharField(blank=True, default='', max_length=255)),
                ('rows_total', models.PositiveIntegerField(default=0)),
                ('rows_matched', models.PositiveIntegerField(default=0)),
                ('rows_committed', models.PositiveIntegerField(default=0)),
                ('parsed_rows', models.JSONField(blank=True, default=list, help_text='Preview payload: one dict per row (employee match, account, branch code, derived bank, status).')),
                ('status', models.CharField(choices=[('pending', 'Pending approval'), ('approved', 'Approved & committed'), ('rejected', 'Rejected')], default='pending', max_length=12)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_at', models.DateTimeField(blank=True, null=True)),
                ('rejection_reason', models.TextField(blank=True, default='')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='bank_import_batches', to=settings.AUTH_USER_MODEL)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bank_imports_approved', to=settings.AUTH_USER_MODEL)),
                ('rejected_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bank_imports_rejected', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Bank Detail Import Batch',
                'verbose_name_plural': 'Bank Detail Import Batches',
                'ordering': ['-created_at'],
            },
        ),
    ]
