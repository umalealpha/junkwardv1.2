"""Add JEClearingRequest (CFO directive 2026-05-26 — maker-checker
reversal queue for duplicate / wrong JEs)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0018_account_unique_name_per_company'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='JEClearingRequest',
            fields=[
                ('id', models.UUIDField(default=__import__('uuid').uuid4,
                                        editable=False, primary_key=True,
                                        serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reason', models.TextField()),
                ('status', models.CharField(
                    choices=[('pending', 'Pending approval'),
                             ('approved', 'Approved & reversed'),
                             ('rejected', 'Rejected')],
                    default='pending', max_length=10,
                )),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decision_note', models.TextField(blank=True, default='')),
                ('journal_entry', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='clearing_requests',
                    to='ledger.journalentry',
                )),
                ('submitted_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='je_clearings_submitted',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('decided_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='je_clearings_decided',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('reversal_entry', models.OneToOneField(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='clearing_request',
                    to='ledger.journalentry',
                )),
            ],
            options={
                'ordering': ['-submitted_at'],
                'indexes': [
                    models.Index(fields=['status', '-submitted_at'],
                                 name='jecr_status_idx'),
                ],
            },
        ),
    ]
