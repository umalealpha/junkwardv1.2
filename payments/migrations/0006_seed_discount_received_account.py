"""
payments 0006 — Seed CoA 400020 "Discount Received".

Idempotent. Creates the account if missing; leaves it alone if present.
Used by Payment.confirm() when an early-payment discount is captured.

If the account is absent at runtime, Payment.confirm() silently SKIPS the
discount JE line and logs a warning — the pay-run still completes. This
migration is the "happy path" seed so prod has the row from day one.
"""
from django.db import migrations


def _seed_discount_received(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    if Account.objects.filter(code='400020').exists():
        return
    # Use other-revenue sub_type to mirror existing 44xx commission income.
    Account.objects.create(
        code='400020',
        name='Discount Received',
        account_type='revenue',
        sub_type='other_revenue',
        statement_class='PNL',
        is_bank_account=False,
        is_active=True,
        description=(
            'Early-payment / settlement discount captured when Alpha Direct '
            'pays a vendor within the discount window of the agreed payment '
            'term. Credited from Payment.confirm() in the AP module.'
        ),
    )


def _noop_reverse(apps, schema_editor):
    """Leave the account in place on reverse — safer than nuking GL rows."""
    return


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0005_payment_batch'),
        ('ledger', '0001_initial'),
        # Account.currency_code defaults to 'BWP' (FK, PROTECT); ensure the
        # Currency rows exist before any Account is created on a fresh DB.
        ('core', '0021_seed_currencies'),
    ]

    operations = [
        migrations.RunPython(_seed_discount_received, _noop_reverse),
    ]
