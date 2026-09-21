import core.crypto_fields
from django.db import migrations


class Migration(migrations.Migration):
    """Widen + switch national_id / bank_account_no to EncryptedCharField (DPA S-5).
    Schema only (varchar 50 -> 255) — safe, no data change. Existing values stay
    plaintext and read fine (the field is read-tolerant); the encrypt_employee_pii
    command back-fills them with round-trip verification."""

    dependencies = [
        ('payroll', '0011_bank_details_import'),
    ]

    operations = [
        migrations.AlterField(
            model_name='employee',
            name='national_id',
            field=core.crypto_fields.EncryptedCharField(
                blank=True, default='', max_length=255,
                help_text='Omang — ENCRYPTED at rest (DPA S-5). Stored locally; not in '
                          'API responses beyond the payslip context.'),
        ),
        migrations.AlterField(
            model_name='employee',
            name='bank_account_no',
            field=core.crypto_fields.EncryptedCharField(blank=True, default='', max_length=255),
        ),
    ]
