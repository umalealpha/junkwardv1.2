"""
core 0021 — Seed the supported Currency rows (BWP + foreign).

Root-cause fix (2026-06-05 audit): `Account.currency_code` is an FK to
core.Currency with `default='BWP'` and `on_delete=PROTECT`, but NO migration
ever created the 'BWP' Currency row. Production only works because the row was
created out-of-band (admin / management command) early in its life. On a fresh
database (test suite, CI, disaster-recovery, new dev env) the first migration
that creates an Account — payments.0006_seed_discount_received_account — dies
with:

    IntegrityError: ledger_account.currency_code_id contains a value 'BWP'
    that does not have a corresponding value in core_currency.code

This migration seeds the currencies from settings.SUPPORTED_CURRENCIES so the
FK default always resolves. It is idempotent (get_or_create), so it is a no-op
on production where the rows already exist.
"""
from django.db import migrations


# (code, name, symbol, decimal_places). Kept in sync with the
# SUPPORTED_CURRENCIES default in alpha_finance/settings.py.
_CURRENCIES = [
    ('BWP', 'Botswana Pula',   'P',  2),
    ('USD', 'US Dollar',       '$',  2),
    ('ZAR', 'South African Rand', 'R', 2),
    ('INR', 'Indian Rupee',    '₹',  2),
    ('ZMW', 'Zambian Kwacha',  'ZK', 2),
]


def _seed_currencies(apps, schema_editor):
    Currency = apps.get_model('core', 'Currency')
    for code, name, symbol, dp in _CURRENCIES:
        Currency.objects.get_or_create(
            code=code,
            defaults={'name': name, 'symbol': symbol,
                      'decimal_places': dp, 'is_active': True},
        )


def _noop_reverse(apps, schema_editor):
    """Leave currency rows in place on reverse — other tables FK to them."""
    return


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_auditlog_download_action'),
    ]

    operations = [
        migrations.RunPython(_seed_currencies, _noop_reverse),
    ]
