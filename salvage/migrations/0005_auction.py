"""
salvage.0005_auction

CFO directive 2026-05-24 — extend the salvage portal with:

  - SalvageAuction + Bid models (timed auction workflow on top of the
    BuyerQuote flow).
  - SalvageInspection model (structured pre-sale inspection report,
    surfaced on the public storefront).
  - PartCategory.expected_recovery_pct (drives reserve auto-suggest).

Schema-only — no data backfill. Reverse is clean (drops models + field).
"""
import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('salvage', '0004_impairment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # -------------------------------------------------------------
        # PartCategory.expected_recovery_pct
        # -------------------------------------------------------------
        migrations.AddField(
            model_name='partcategory',
            name='expected_recovery_pct',
            field=models.DecimalField(
                max_digits=5, decimal_places=4,
                default=Decimal('0.3000'),
                help_text=(
                    'Default expected recovery as a fraction of claim '
                    'gross. Drives suggest_reserve().'
                ),
            ),
        ),

        # -------------------------------------------------------------
        # SalvageInspection
        # -------------------------------------------------------------
        migrations.CreateModel(
            name='SalvageInspection',
            fields=[
                ('id', models.UUIDField(
                    primary_key=True, default=uuid.uuid4,
                    editable=False, serialize=False,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('inspected_at', models.DateTimeField()),
                ('runs_drives', models.BooleanField(default=False)),
                ('mileage_km', models.PositiveIntegerField(blank=True, null=True)),
                ('key_present', models.BooleanField(default=False)),
                ('body_panels_jsonb', models.JSONField(blank=True, default=dict)),
                ('engine_status', models.CharField(blank=True, default='', max_length=80)),
                ('transmission_status', models.CharField(blank=True, default='', max_length=80)),
                ('airbags_deployed', models.BooleanField(default=False)),
                ('salvage_title_status', models.CharField(
                    choices=[
                        ('clean',   'Clean title'),
                        ('salvage', 'Salvage title'),
                        ('rebuilt', 'Rebuilt title'),
                        ('junk',    'Junk title'),
                    ],
                    default='salvage', max_length=12,
                )),
                ('notes', models.TextField(blank=True, default='')),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='inspections',
                    to='salvage.salvageitem',
                )),
                ('inspected_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvage_inspections',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-inspected_at', '-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='salvageinspection',
            index=models.Index(fields=['item'], name='salv_insp_item_idx'),
        ),
        migrations.AddIndex(
            model_name='salvageinspection',
            index=models.Index(fields=['inspected_at'], name='salv_insp_at_idx'),
        ),

        # -------------------------------------------------------------
        # SalvageAuction (current_high_bid added after Bid exists)
        # -------------------------------------------------------------
        migrations.CreateModel(
            name='SalvageAuction',
            fields=[
                ('id', models.UUIDField(
                    primary_key=True, default=uuid.uuid4,
                    editable=False, serialize=False,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('opens_at', models.DateTimeField()),
                ('closes_at', models.DateTimeField()),
                ('min_bid', models.DecimalField(
                    decimal_places=2, default=Decimal('0'), max_digits=18,
                    help_text='Minimum opening bid amount (BWP).',
                )),
                ('status', models.CharField(
                    choices=[
                        ('scheduled', 'Scheduled'),
                        ('live',      'Live'),
                        ('closed',    'Closed'),
                        ('cancelled', 'Cancelled'),
                    ],
                    default='scheduled', max_length=12,
                )),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='auctions',
                    to='salvage.salvageitem',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvage_auctions_created',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),

        # -------------------------------------------------------------
        # Bid
        # -------------------------------------------------------------
        migrations.CreateModel(
            name='Bid',
            fields=[
                ('id', models.UUIDField(
                    primary_key=True, default=uuid.uuid4,
                    editable=False, serialize=False,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('placed_at', models.DateTimeField(auto_now_add=True)),
                ('withdrawn', models.BooleanField(default=False)),
                ('auction', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='bids',
                    to='salvage.salvageauction',
                )),
                ('buyer', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='bids',
                    to='salvage.buyerquote',
                    help_text='Buyer identity sourced from BuyerQuote row.',
                )),
            ],
            options={
                'ordering': ['-amount', '-placed_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='bid',
            index=models.Index(fields=['auction'], name='salv_bid_auction_idx'),
        ),
        migrations.AddIndex(
            model_name='bid',
            index=models.Index(fields=['withdrawn'], name='salv_bid_withdrawn_idx'),
        ),

        # -------------------------------------------------------------
        # Now wire SalvageAuction.current_high_bid → Bid (added last so
        # the Bid table exists when the FK is created).
        # -------------------------------------------------------------
        migrations.AddField(
            model_name='salvageauction',
            name='current_high_bid',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='salvage.bid',
                help_text='Pointer to the highest non-withdrawn bid.',
            ),
        ),
        migrations.AddIndex(
            model_name='salvageauction',
            index=models.Index(fields=['status'], name='salv_auc_status_idx'),
        ),
        migrations.AddIndex(
            model_name='salvageauction',
            index=models.Index(fields=['item'], name='salv_auc_item_idx'),
        ),
        migrations.AddIndex(
            model_name='salvageauction',
            index=models.Index(fields=['closes_at'], name='salv_auc_closes_idx'),
        ),
    ]
