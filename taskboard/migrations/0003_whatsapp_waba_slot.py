"""Seed the WhatsApp Business Account id vault slot (CFO 2026-07-14).

Message-template submit/read use the WABA id (distinct from the sender phone
id). Pre-create the slot empty so it shows up ready to fill in Settings → Secrets.
"""
from django.db import migrations


def seed_waba_slot(apps, schema_editor):
    VaultSecret = apps.get_model('core', 'VaultSecret')
    if not VaultSecret.objects.filter(name__iexact='WHATSAPP_WABA_ID').exists():
        VaultSecret.objects.create(
            name='WHATSAPP_WABA_ID', category='api', secret_ciphertext='',
            notes='WhatsApp Business Account id (Meta) — used to submit/approve '
                  'message templates. Paste via Rotate.',
        )


def unseed_waba_slot(apps, schema_editor):
    VaultSecret = apps.get_model('core', 'VaultSecret')
    VaultSecret.objects.filter(name='WHATSAPP_WABA_ID').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0002_whatsapp'),
    ]

    operations = [
        migrations.RunPython(seed_waba_slot, unseed_waba_slot),
    ]
