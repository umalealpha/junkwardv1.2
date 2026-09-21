"""
0005_vendor_kyc — Vendor KYC counterparty due-diligence record.

Adds VendorKYC (procurement/kyc_models.py) attached 1:1 to billing.Contact.
The Contact.kyc_status property is installed in code (procurement/models.py)
because billing/ is bible-protected — no schema change to Contact itself.

Hook: payments/models.py::Payment.confirm() calls
procurement.kyc_service.check_kyc_for_payment(self) at the top of the method.
That is a code-only change with no migration.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models

import procurement.kyc_models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0008_payment_terms'),
        ('procurement', '0004_poline_account_optional'),
    ]

    operations = [
        migrations.CreateModel(
            name='VendorKYC',
            fields=[
                ('id', models.UUIDField(
                    default=uuid.uuid4,
                    editable=False,
                    primary_key=True,
                    serialize=False,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tin_attachment', models.FileField(
                    blank=True, null=True,
                    upload_to=procurement.kyc_models._kyc_upload_to,
                    help_text='Tax Identification Number certificate (BURS).',
                )),
                ('coi_attachment', models.FileField(
                    blank=True, null=True,
                    upload_to=procurement.kyc_models._kyc_upload_to,
                    help_text='Certificate of Incorporation.',
                )),
                ('bank_letter_attachment', models.FileField(
                    blank=True, null=True,
                    upload_to=procurement.kyc_models._kyc_upload_to,
                    help_text='Bank confirmation letter — verifies the account details '
                              'we pay into are owned by this counterparty.',
                )),
                ('beneficial_owners', models.JSONField(
                    blank=True, default=list,
                    help_text='List of ultimate beneficial owners. Each entry should be '
                              'a dict: {"name": str, "id_number": str, "pct": float, '
                              '"role": str}. Empty list = none captured yet.',
                )),
                ('sanctions_checked_at', models.DateTimeField(
                    blank=True, null=True,
                    help_text='When the most recent sanctions screening was run.',
                )),
                ('sanctions_status', models.CharField(
                    choices=[
                        ('clean', 'Clean — screened, no match'),
                        ('flagged', 'Flagged — name match requires review'),
                        ('unchecked', 'Unchecked — screening not yet run'),
                    ],
                    default='unchecked',
                    max_length=10,
                )),
                ('pep_status', models.CharField(
                    choices=[
                        ('none', 'Not a PEP'),
                        ('pep', 'Politically Exposed Person'),
                        ('associate', 'Close associate / family of a PEP'),
                        ('unchecked', 'Not yet screened'),
                    ],
                    default='unchecked',
                    max_length=10,
                )),
                ('kyc_expires_on', models.DateField(
                    blank=True, null=True,
                    help_text='Date this KYC record expires and must be re-validated. '
                              'NULL = no expiry set (treated as ok for the threshold guard).',
                )),
                ('notes', models.TextField(blank=True, default='')),
                ('contact', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='kyc',
                    to='billing.contact',
                    help_text='The counterparty this KYC record covers.',
                )),
            ],
            options={
                'verbose_name': 'Vendor KYC',
                'verbose_name_plural': 'Vendor KYC records',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='vendorkyc',
            index=models.Index(fields=['sanctions_status'], name='proc_kyc_sanc_idx'),
        ),
        migrations.AddIndex(
            model_name='vendorkyc',
            index=models.Index(fields=['pep_status'], name='proc_kyc_pep_idx'),
        ),
        migrations.AddIndex(
            model_name='vendorkyc',
            index=models.Index(fields=['kyc_expires_on'], name='proc_kyc_expires_idx'),
        ),
    ]
