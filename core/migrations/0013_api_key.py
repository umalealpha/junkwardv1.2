"""
0013_api_key — permanent ApiKey for headless automation agents.
"""

import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_admin_allowlist_entry'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ApiKey',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('label',         models.CharField(max_length=120)),
                ('key_prefix',    models.CharField(max_length=12, db_index=True)),
                ('key_hash',      models.CharField(max_length=255)),
                ('allowed_scopes', models.JSONField(default=list,
                                       help_text='List of scope strings — e.g. ["smart-upload"].')),
                ('is_active',     models.BooleanField(default=True)),
                ('last_used_at',  models.DateTimeField(null=True, blank=True)),
                ('last_used_ip',  models.CharField(max_length=64, blank=True, default='')),
                ('service_user',  models.ForeignKey(
                                    on_delete=models.PROTECT,
                                    to=settings.AUTH_USER_MODEL,
                                    related_name='api_keys',
                                    help_text='Service-account user the key authenticates as.')),
                ('created_by',    models.ForeignKey(
                                    null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    to=settings.AUTH_USER_MODEL,
                                    related_name='api_keys_created')),
            ],
            options={
                'verbose_name':        'API key',
                'verbose_name_plural': 'API keys',
                'ordering':            ['-created_at'],
            },
        ),
    ]
