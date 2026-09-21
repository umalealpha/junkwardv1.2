# Generated 2026-06-09 — restored after empty-file deploy crash.
# ChatMessage model lives at core/models.py:1407. Migration was committed
# in d2bcc9f (chat widget feature) as a 0-byte file. This restores the
# CreateModel operation. Deps include OmniTask (FK target).

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_vaultsecret_alter_onlinepresence_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatMessage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('room', models.CharField(db_index=True, default='general', max_length=40)),
                ('body', models.TextField()),
                ('is_task', models.BooleanField(default=False)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='chat_messages', to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='chat_messages', to='core.omnitask')),
            ],
            options={
                'verbose_name': 'Chat Message',
                'verbose_name_plural': 'Chat Messages',
                'ordering': ['created_at'],
                'abstract': False,
                'indexes': [models.Index(fields=['room', 'created_at'], name='core_chatme_room_3a6dd2_idx')],
            },
        ),
    ]
