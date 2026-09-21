# FA-001 intercompany account policy (CFO directive 2026-05-29).
# Singleton holding the receivable + payable GL codes used by intercompany
# fixed-asset transfers (and any future intercompany flow). Defaults NULL —
# CFO populates at /admin/core/intercompanyaccountpolicy/ or via settings UI.
# fx.services.assert_approved_rates_exist pattern: posting blocks until set.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_exchangerate_approval'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='IntercompanyAccountPolicy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('receivable_account_code', models.CharField(
                    blank=True, default='', max_length=20,
                    help_text='GL code for "Due from related companies" — debited in the sender JE '
                              'of an intercompany asset transfer. Leave blank until CFO confirms '
                              'the code; transfers block when unset (fail-safe).')),
                ('payable_account_code', models.CharField(
                    blank=True, default='', max_length=20,
                    help_text='GL code for "Due to related companies" — credited in the receiver JE '
                              'of an intercompany asset transfer. Leave blank until CFO confirms.')),
                ('notes', models.TextField(blank=True, default='')),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='intercompany_policy_updates',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name':        'Intercompany Account Policy',
                'verbose_name_plural': 'Intercompany Account Policy',
                'ordering':            ['-created_at'],
                'abstract':            False,
            },
        ),
    ]
