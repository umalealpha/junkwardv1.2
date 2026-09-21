"""Initial migration for reinsurance app."""
import uuid
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0001_initial'),
        ('ledger', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Reinsurer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200, unique=True)),
                ('short_code', models.CharField(max_length=20, unique=True)),
                ('country', models.CharField(default='BW', max_length=2)),
                ('credit_rating', models.CharField(blank=True, max_length=10)),
                ('is_active', models.BooleanField(default=True)),
                ('notes', models.TextField(blank=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='ReinsuranceTreaty',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('treaty_number', models.CharField(max_length=40, unique=True)),
                ('description', models.CharField(max_length=200)),
                ('treaty_type', models.CharField(choices=[
                    ('quota_share', 'Quota Share (proportional)'),
                    ('surplus', 'Surplus (proportional)'),
                    ('xl', 'Excess of Loss (non-proportional)'),
                    ('stop_loss', 'Stop Loss (non-proportional)'),
                    ('facultative', 'Facultative'),
                ], max_length=20)),
                ('line_of_business', models.CharField(max_length=80)),
                ('inception_date', models.DateField()),
                ('expiry_date', models.DateField()),
                ('cession_share_percent', models.DecimalField(blank=True, decimal_places=4, max_digits=6, null=True,
                    validators=[django.core.validators.MinValueValidator(Decimal('0')),
                                django.core.validators.MaxValueValidator(Decimal('100'))])),
                ('commission_percent', models.DecimalField(blank=True, decimal_places=4, max_digits=6, null=True,
                    validators=[django.core.validators.MinValueValidator(Decimal('0')),
                                django.core.validators.MaxValueValidator(Decimal('100'))])),
                ('retention_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('limit_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('status', models.CharField(choices=[
                    ('draft', 'Draft'), ('active', 'Active'),
                    ('expired', 'Expired'), ('cancelled', 'Cancelled'),
                ], default='draft', max_length=12)),
                ('notes', models.TextField(blank=True)),
                ('reinsurer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                                related_name='treaties', to='reinsurance.reinsurer')),
                ('currency_code', models.ForeignKey(default='BWP', on_delete=django.db.models.deletion.PROTECT,
                                                    to='core.currency')),
            ],
            options={'ordering': ['-inception_date', 'treaty_number']},
        ),
        migrations.CreateModel(
            name='BordereauImport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('bordereau_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('received_date', models.DateField(default=timezone.now)),
                ('file_name', models.CharField(blank=True, max_length=240)),
                ('line_count', models.PositiveIntegerField(default=0)),
                ('total_gross_premium', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('total_ceded_premium', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('total_commission', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('status', models.CharField(choices=[
                    ('uploaded', 'Uploaded'), ('parsed', 'Parsed'),
                    ('committed', 'Committed'), ('rejected', 'Rejected'),
                ], default='uploaded', max_length=12)),
                ('notes', models.TextField(blank=True)),
                ('treaty', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='bordereau_imports',
                                              to='reinsurance.reinsurancetreaty')),
            ],
            options={'ordering': ['-received_date', '-bordereau_number']},
        ),
        migrations.CreateModel(
            name='Cession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('cession_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('cession_date', models.DateField(default=timezone.now)),
                ('policy_reference', models.CharField(blank=True, max_length=120)),
                ('risk_description', models.CharField(blank=True, max_length=200)),
                ('gross_premium', models.DecimalField(decimal_places=2, max_digits=18)),
                ('ceded_premium', models.DecimalField(decimal_places=2, max_digits=18)),
                ('commission_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('status', models.CharField(choices=[
                    ('draft', 'Draft'), ('posted', 'Posted'), ('voided', 'Voided'),
                ], default='draft', max_length=10)),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
                ('treaty', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='cessions',
                                              to='reinsurance.reinsurancetreaty')),
                ('bordereau', models.ForeignKey(blank=True, null=True,
                                                 on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='cessions',
                                                 to='reinsurance.bordereauimport')),
                ('journal_entry', models.OneToOneField(blank=True, null=True,
                                                       on_delete=django.db.models.deletion.SET_NULL,
                                                       related_name='reinsurance_cession',
                                                       to='ledger.journalentry')),
                ('posted_by', models.ForeignKey(blank=True, null=True,
                                                 on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='cessions_posted',
                                                 to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-cession_date', '-cession_number'],
                'indexes': [
                    models.Index(fields=['status'], name='reins_cess_status_idx'),
                    models.Index(fields=['treaty', 'status'], name='reins_cess_treaty_st_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='ReinsuranceRecovery',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('recovery_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('recovery_date', models.DateField(default=timezone.now)),
                ('claim_reference', models.CharField(max_length=120)),
                ('gross_loss', models.DecimalField(decimal_places=2, max_digits=18)),
                ('ceded_recovery', models.DecimalField(decimal_places=2, max_digits=18)),
                ('notes', models.TextField(blank=True)),
                ('status', models.CharField(choices=[
                    ('draft', 'Draft'), ('posted', 'Posted'),
                    ('settled', 'Settled (cash received)'), ('voided', 'Voided'),
                ], default='draft', max_length=10)),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
                ('treaty', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                              related_name='recoveries',
                                              to='reinsurance.reinsurancetreaty')),
                ('journal_entry', models.OneToOneField(blank=True, null=True,
                                                       on_delete=django.db.models.deletion.SET_NULL,
                                                       related_name='reinsurance_recovery',
                                                       to='ledger.journalentry')),
                ('posted_by', models.ForeignKey(blank=True, null=True,
                                                 on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='recoveries_posted',
                                                 to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-recovery_date', '-recovery_number'],
                'indexes': [
                    models.Index(fields=['status'], name='reins_rec_status_idx'),
                    models.Index(fields=['treaty', 'status'], name='reins_rec_treaty_st_idx'),
                ],
            },
        ),
    ]
