# Asset hand-overs (CFO 2026-06-26): structured current holder on Asset
# + append-only AssetAssignment custody history.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0009_intercompany_transfer'),
        ('payroll', '0009_payslip_source_currency'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='custodian_employee',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='assets_held', to='payroll.employee',
                help_text='Staff member currently holding this asset.',
            ),
        ),
        migrations.CreateModel(
            name='AssetAssignment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('from_custodian', models.CharField(blank=True, default='', max_length=200)),
                ('to_custodian', models.CharField(blank=True, default='', max_length=200)),
                ('from_location', models.CharField(blank=True, default='', max_length=200)),
                ('to_location', models.CharField(blank=True, default='', max_length=200)),
                ('reason', models.CharField(blank=True, default='', max_length=300)),
                ('transferred_at', models.DateField()),
                ('asset', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='assignments', to='assets.asset',
                )),
                ('from_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='asset_transfers_out', to='payroll.employee',
                )),
                ('to_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='asset_transfers_in', to='payroll.employee',
                )),
                ('transferred_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='asset_transfers_made', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Asset Assignment',
                'verbose_name_plural': 'Asset Assignments',
                'ordering': ['-transferred_at', '-created_at'],
                'abstract': False,
            },
        ),
    ]
