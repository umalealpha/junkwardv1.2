from django.db import migrations, models


class Migration(migrations.Migration):
    """Finance directive 2026-07-02: voucher clearing now REVERSES posted JEs
    instead of hard-deleting them. Only the ``status`` choice label changes
    ('Approved & deleted' -> 'Approved & reversed'); state-only, no SQL."""

    dependencies = [
        ('ledger', '0024_fiscalperiod_auto_lock_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='jeclearingrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending approval'),
                    ('approved', 'Approved & reversed'),
                    ('rejected', 'Rejected'),
                ],
                default='pending',
                max_length=10,
            ),
        ),
    ]
