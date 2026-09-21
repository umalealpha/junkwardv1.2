"""Re-fingerprint existing refunds after the leading-zero canonicalisation change.

account_fingerprint() now strips leading zeros before hashing, to match Graphite.
That changes the output for every account written with a leading zero EVEN WITH
NO KEY CHANGE — so on deploy, those stored fingerprints stop matching newly
computed ones and the same-account fraud check goes quiet on exactly those
accounts. It raises nothing: the lookup just returns no rows.

Healing it here rather than in a runbook step, so it cannot be forgotten between
the deploy and someone remembering to run a command.

This handles the CANONICALISATION change only. The separate re-key for the
shared key (REFUND_ACCOUNT_INDEX_KEY, agreed with Graphite) still runs
afterwards via `manage.py rekey_account_fingerprints --commit`, once the key is
actually set. Both are idempotent and re-runnable.
"""
from django.db import migrations


def recanonicalise(apps, schema_editor):
    # The historical model has no methods, so decrypt + fingerprint directly.
    from core.vault_crypto import decrypt
    from customer_refunds.models import account_fingerprint

    CustomerRefund = apps.get_model('customer_refunds', 'CustomerRefund')
    to_update = []
    for r in CustomerRefund.objects.exclude(account_number_enc='').only(
            'id', 'account_number_enc', 'account_fingerprint'):
        raw = decrypt(r.account_number_enc)
        if not raw:
            # decrypt() RETURNS '' on a bad or retired key, it does not raise.
            # Writing '' here would drop the refund out of the fraud check
            # entirely — worse than leaving it on the old fingerprint. Skip it;
            # the management command reports these rows out loud.
            continue
        new_fp = account_fingerprint(raw)
        if new_fp != r.account_fingerprint:
            r.account_fingerprint = new_fp
            to_update.append(r)
    if to_update:
        CustomerRefund.objects.bulk_update(
            to_update, ['account_fingerprint'], batch_size=200)


def noop(apps, schema_editor):
    """Deliberately irreversible in data terms.

    Going backwards would mean re-hashing under the OLD canonicalisation, which
    is the broken state. Reversing the migration leaves the corrected
    fingerprints in place; that is safe, because they are recomputed from the
    ciphertext and never read as history.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('customer_refunds', '0003_branch_name'),
    ]

    operations = [
        migrations.RunPython(recanonicalise, noop),
    ]
