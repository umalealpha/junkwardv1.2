"""WhatsApp reminder console (CFO directive 2026-07-14).

Adds the CFO-only staff phonebook (WhatsAppContact) + a send audit log
(WhatsAppMessage), and pre-creates two EMPTY Secrets-Vault slots so the API
key + sender phone-id show up ready to fill in Settings → Secrets — mirroring
how the LLM key vault seeds its slots.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_vault_slots(apps, schema_editor):
    """Create empty WHATSAPP_TOKEN / WHATSAPP_PHONE_ID vault entries (category
    API) if they don't exist. Left blank — the CFO fills them via the Secrets UI
    (Rotate). Blank ciphertext keeps get_llm_key() returning '' until set."""
    VaultSecret = apps.get_model('core', 'VaultSecret')
    for name, notes in (
        ('WHATSAPP_TOKEN',
         'WhatsApp Cloud API access token — used to send task reminders. Paste via Rotate.'),
        ('WHATSAPP_PHONE_ID',
         'WhatsApp Cloud API sender phone-number id (from Meta). Paste via Rotate.'),
    ):
        if not VaultSecret.objects.filter(name__iexact=name).exists():
            VaultSecret.objects.create(
                name=name, category='api', notes=notes, secret_ciphertext='',
            )


def unseed_vault_slots(apps, schema_editor):
    VaultSecret = apps.get_model('core', 'VaultSecret')
    VaultSecret.objects.filter(name__in=['WHATSAPP_TOKEN', 'WHATSAPP_PHONE_ID']).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0037_omnitask_performance_points'),
        ('taskboard', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='WhatsAppContact',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('phone', models.CharField(help_text='E.164, e.g. +2677xxxxxxx (8-digit local numbers auto-prefixed 267).', max_length=32)),
                ('role', models.CharField(blank=True, default='', help_text="Free text, e.g. 'Claims Manager'.", max_length=120)),
                ('is_manager', models.BooleanField(default=False, help_text='Flag managers so reminders can target them first.')),
                ('active', models.BooleanField(default=True)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='whatsapp_contacts_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'WhatsApp Contact',
                'verbose_name_plural': 'WhatsApp Contacts',
                'ordering': ['-is_manager', 'name'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='WhatsAppMessage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('to_name', models.CharField(blank=True, default='', max_length=160)),
                ('to_phone', models.CharField(max_length=32)),
                ('body', models.TextField()),
                ('status', models.CharField(choices=[('sent', 'Sent'), ('failed', 'Failed'), ('queued', 'Queued')], default='queued', max_length=8)),
                ('provider_id', models.CharField(blank=True, default='', help_text='Message id returned by the WhatsApp API.', max_length=160)),
                ('error', models.TextField(blank=True, default='')),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='whatsapp_messages_sent', to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='whatsapp_reminders', to='core.omnitask')),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.RunPython(seed_vault_slots, unseed_vault_slots),
    ]
