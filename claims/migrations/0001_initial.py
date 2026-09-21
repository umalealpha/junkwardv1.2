# Hand-authored initial migration for claims (Subrogation + Salvage + import).

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('billing', '0003_contact_related_party'),
        ('core', '0003_userprofile_title_isadmin'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Subrogation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('claim_reference', models.CharField(max_length=80)),
                ('incident_date', models.DateField(blank=True, null=True)),
                ('third_party_name', models.CharField(max_length=200)),
                ('third_party_insurer', models.CharField(blank=True, default='', max_length=200)),
                ('claim_paid_amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('expected_recovery', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('actual_recovery', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('last_recovery_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending — pursuing recovery'),
                        ('partial', 'Partially recovered'),
                        ('fully_recovered', 'Fully recovered'),
                        ('written_off', 'Written off — no recovery'),
                        ('in_litigation', 'In litigation'),
                    ],
                    default='pending', max_length=20,
                )),
                ('notes', models.TextField(blank=True, default='')),
                ('graphite_id', models.CharField(blank=True, default='', max_length=100)),
                ('company', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='subrogations', to='core.company')),
                ('third_party_contact', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='subrogations', to='billing.contact')),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='subrogations_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Subrogation',
                'verbose_name_plural': 'Subrogations',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='subrogation',
            index=models.Index(fields=['claim_reference'], name='claims_sub_claim_r_idx'),
        ),
        migrations.AddIndex(
            model_name='subrogation',
            index=models.Index(fields=['status'], name='claims_sub_status_idx'),
        ),
        migrations.CreateModel(
            name='Salvage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('claim_reference', models.CharField(max_length=80)),
                ('incident_date', models.DateField(blank=True, null=True)),
                ('asset_description', models.CharField(max_length=300)),
                ('estimated_value', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('sale_proceeds', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('sale_date', models.DateField(blank=True, null=True)),
                ('buyer_name', models.CharField(blank=True, default='', max_length=200)),
                ('status', models.CharField(
                    choices=[
                        ('pending',  'Pending — awaiting decision'),
                        ('for_sale', 'For sale'),
                        ('sold',     'Sold'),
                        ('scrapped', 'Scrapped'),
                        ('retained', 'Retained by insured'),
                    ],
                    default='pending', max_length=20,
                )),
                ('notes', models.TextField(blank=True, default='')),
                ('graphite_id', models.CharField(blank=True, default='', max_length=100)),
                ('company', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='salvages', to='core.company')),
                ('buyer_contact', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvages_purchased', to='billing.contact')),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='salvages_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Salvage',
                'verbose_name_plural': 'Salvages',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='salvage',
            index=models.Index(fields=['claim_reference'], name='claims_sal_claim_r_idx'),
        ),
        migrations.AddIndex(
            model_name='salvage',
            index=models.Index(fields=['status'], name='claims_sal_status_idx'),
        ),
        migrations.CreateModel(
            name='RecoveryImportBatch',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('subrogation', 'Subrogation'), ('salvage', 'Salvage')], max_length=15)),
                ('file_name', models.CharField(blank=True, default='', max_length=255)),
                ('rows_total', models.PositiveIntegerField(default=0)),
                ('rows_valid', models.PositiveIntegerField(default=0)),
                ('rows_invalid', models.PositiveIntegerField(default=0)),
                ('rows_skipped_dup', models.PositiveIntegerField(default=0)),
                ('rows_imported', models.PositiveIntegerField(default=0)),
                ('parsed_rows', models.JSONField(blank=True, default=list)),
                ('validation_errors', models.JSONField(blank=True, default=list)),
                ('status', models.CharField(
                    choices=[('draft', 'Draft (preview)'), ('committed', 'Committed'), ('failed', 'Failed')],
                    default='draft', max_length=15,
                )),
                ('committed_at', models.DateTimeField(blank=True, null=True)),
                ('company', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='recovery_import_batches', to='core.company')),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='recovery_import_batches', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Recovery Import Batch',
                'verbose_name_plural': 'Recovery Import Batches',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
    ]
