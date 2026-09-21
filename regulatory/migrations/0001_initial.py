# Hand-authored initial migration for the regulatory app.

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0003_userprofile_title_isadmin'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CapitalRequirementParameter',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=60, unique=True)),
                ('label', models.CharField(max_length=200)),
                ('value', models.CharField(max_length=200)),
                ('notes', models.TextField(blank=True, default='')),
                ('effective_from', models.DateField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Capital Requirement Parameter',
                'verbose_name_plural': 'Capital Requirement Parameters',
                'ordering': ['code'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RegulatoryCapitalSnapshot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('as_of_date', models.DateField()),
                ('available_capital', models.DecimalField(decimal_places=2, max_digits=18)),
                ('required_capital', models.DecimalField(decimal_places=2, max_digits=18)),
                ('capital_adequacy_ratio', models.DecimalField(decimal_places=4, max_digits=10)),
                ('status', models.CharField(
                    choices=[
                        ('compliant', 'Compliant'),
                        ('margin', 'Margin (watch list)'),
                        ('breach', 'BREACH'),
                        ('draft', 'Draft'),
                    ],
                    default='draft', max_length=15,
                )),
                ('components', models.JSONField(blank=True, default=dict)),
                ('notes', models.TextField(blank=True, default='')),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='capital_snapshots_approved',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('company', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='capital_snapshots', to='core.company',
                )),
                ('prepared_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='capital_snapshots_prepared',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Regulatory Capital Snapshot',
                'verbose_name_plural': 'Regulatory Capital Snapshots',
                'ordering': ['-as_of_date', '-created_at'],
                'abstract': False,
            },
        ),
    ]
