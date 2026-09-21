"""
0012_admin_allowlist_entry — table for self-service admin IP allowlist.
"""

import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_company_entity_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminAllowlistEntry',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cidr',       models.CharField(max_length=64, unique=True,
                                                help_text='IPv4/IPv6 CIDR. /32 for a single host.')),
                ('label',      models.CharField(max_length=120, blank=True, default='',
                                                help_text='Where this IP belongs (e.g. "CFO desk").')),
                ('is_active',  models.BooleanField(default=True)),
                ('created_by', models.ForeignKey(
                                    null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    to=settings.AUTH_USER_MODEL,
                                    related_name='admin_allowlist_entries')),
            ],
            options={
                'verbose_name':        'Admin allowlist entry',
                'verbose_name_plural': 'Admin allowlist entries',
                'ordering':            ['-created_at'],
            },
        ),
    ]
