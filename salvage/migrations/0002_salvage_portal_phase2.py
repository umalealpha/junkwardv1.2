"""Salvage Portal — Phase 2: BuyerQuote, Sale, SalvageApproval + item fields.

Adds the missing surface from motor-liquidators that Phase 1 deferred:

  - BuyerQuote (public-facing offers, staff review queue)
  - Sale (terminal posting after approval)
  - SalvageApproval (EXCO approval workflow)
  - SalvageItem.yard_section, shelf_row, received_date, sold_date,
    disposed_date, received_by, notes
  - Status enum expands: + QUOTED, WRITTEN_OFF, DISPOSED
    (legacy AVAILABLE/RESERVED/SOLD/SCRAPPED/ON_HOLD retained)

See docs/ (or .claude/specs/salvage-portal/design.md) for the workflow.
"""
import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('ledger', '0001_initial'),
        ('salvage', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # -------------------------------------------------------------
        # SalvageItem — new fields + status enum expansion
        # -------------------------------------------------------------
        migrations.AlterField(
            model_name='salvageitem',
            name='status',
            field=models.CharField(
                choices=[
                    ('available',   'Available'),
                    ('quoted',      'Quoted (offer received)'),
                    ('reserved',    'Reserved'),
                    ('sold',        'Sold'),
                    ('written_off', 'Written off'),
                    ('disposed',    'Disposed'),
                    ('scrapped',    'Scrapped'),
                    ('on_hold',     'On hold'),
                ],
                default='available',
                max_length=15,
            ),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='yard_section',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='shelf_row',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='received_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='sold_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='disposed_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='received_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='salvage_items_received',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='notes',
            field=models.TextField(blank=True, default=''),
        ),

        # -------------------------------------------------------------
        # BuyerQuote
        # -------------------------------------------------------------
        migrations.CreateModel(
            name='BuyerQuote',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('buyer_name', models.CharField(max_length=200)),
                ('buyer_email', models.EmailField(blank=True, default='', max_length=254)),
                ('buyer_phone', models.CharField(max_length=40)),
                ('buyer_company', models.CharField(blank=True, default='', max_length=200)),
                ('offered_price', models.DecimalField(max_digits=18, decimal_places=2)),
                ('message', models.TextField(blank=True, default='')),
                ('status', models.CharField(
                    choices=[
                        ('pending',      'Pending review'),
                        ('under_review', 'Under review'),
                        ('accepted',     'Accepted'),
                        ('rejected',     'Rejected'),
                        ('countered',    'Countered'),
                    ],
                    default='pending', max_length=15,
                )),
                ('counter_price', models.DecimalField(
                    blank=True, null=True,
                    decimal_places=2, max_digits=18,
                    help_text='Set when status=countered',
                )),
                ('review_notes', models.TextField(blank=True, default='')),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('submitter_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('submitter_ua', models.CharField(blank=True, default='', max_length=400)),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='buyer_quotes',
                    to='salvage.salvageitem',
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvage_quotes_reviewed',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='buyerquote',
            index=models.Index(fields=['status'], name='salv_bq_status_idx'),
        ),
        migrations.AddIndex(
            model_name='buyerquote',
            index=models.Index(fields=['item'], name='salv_bq_item_idx'),
        ),

        # -------------------------------------------------------------
        # Sale
        # -------------------------------------------------------------
        migrations.CreateModel(
            name='Sale',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('buyer_name', models.CharField(max_length=200)),
                ('buyer_phone', models.CharField(blank=True, default='', max_length=40)),
                ('buyer_email', models.EmailField(blank=True, default='', max_length=254)),
                ('sale_price', models.DecimalField(max_digits=18, decimal_places=2)),
                ('payment_method', models.CharField(
                    choices=[
                        ('cash',         'Cash'),
                        ('eft',          'EFT / bank transfer'),
                        ('cheque',       'Cheque'),
                        ('mobile_money', 'Mobile money'),
                        ('card',         'Card'),
                    ],
                    max_length=15,
                )),
                ('payment_ref', models.CharField(blank=True, default='', max_length=120)),
                ('sale_date', models.DateField()),
                ('notes', models.TextField(blank=True, default='')),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='sales',
                    to='salvage.salvageitem',
                )),
                ('buyer_quote', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='sales',
                    to='salvage.buyerquote',
                )),
                ('approved_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvage_sales_approved',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('sold_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvage_sales_sold',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('journal_entry', models.OneToOneField(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvage_sale',
                    to='ledger.journalentry',
                )),
            ],
            options={
                'ordering': ['-sale_date', '-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='sale',
            index=models.Index(fields=['sale_date'], name='salv_sale_date_idx'),
        ),
        migrations.AddIndex(
            model_name='sale',
            index=models.Index(fields=['item'], name='salv_sale_item_idx'),
        ),

        # -------------------------------------------------------------
        # SalvageApproval
        # -------------------------------------------------------------
        migrations.CreateModel(
            name='SalvageApproval',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(
                    choices=[
                        ('sale',         'Sale below reserve'),
                        ('quote_accept', 'Quote acceptance'),
                        ('disposal',     'Disposal / write-off'),
                    ],
                    default='sale', max_length=15,
                )),
                ('status', models.CharField(
                    choices=[
                        ('pending',  'Pending'),
                        ('approved', 'Approved'),
                        ('rejected', 'Rejected'),
                    ],
                    default='pending', max_length=15,
                )),
                ('requested_amount', models.DecimalField(
                    decimal_places=2, default=Decimal('0'), max_digits=18,
                )),
                ('threshold_amount', models.DecimalField(
                    decimal_places=2, default=Decimal('0'), max_digits=18,
                    help_text='Approval threshold in force at request time',
                )),
                ('notes', models.TextField(blank=True, default='')),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('item', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='approvals',
                    to='salvage.salvageitem',
                )),
                ('buyer_quote', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='approvals',
                    to='salvage.buyerquote',
                )),
                ('sale', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='approvals',
                    to='salvage.sale',
                )),
                ('requested_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='salvage_approvals_requested',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('approved_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='salvage_approvals_resolved',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='salvageapproval',
            index=models.Index(fields=['status'], name='salv_appr_status_idx'),
        ),
        migrations.AddIndex(
            model_name='salvageapproval',
            index=models.Index(fields=['kind'], name='salv_appr_kind_idx'),
        ),
    ]
