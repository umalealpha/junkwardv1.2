"""
0003_phase_b_manus_audit — approval delegation, PO amendments, PO
attachments, blanket POs, PO expiry, capex auto-link.

CFO directive 2026-05-20 (Manus PO Audit Part B closeout).
"""

import uuid

from django.conf import settings
from django.db import migrations, models

import procurement.models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0002_purchaseorder_commitment_journal_entry'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── New PO fields ──────────────────────────────────────────
        migrations.AddField(
            model_name='purchaseorder',
            name='is_blanket',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='blanket_limit',
            field=models.DecimalField(
                max_digits=18, decimal_places=2,
                default=0,
                help_text='BWP ceiling for blanket draws. Ignored when is_blanket=False.',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='valid_until',
            field=models.DateField(
                null=True, blank=True,
                help_text='Optional. After this date the PO becomes eligible for auto-expiry.',
            ),
        ),
        migrations.AddField(
            model_name='purchaseorder',
            name='amendment_version',
            field=models.PositiveSmallIntegerField(default=1),
        ),
        # ── EXPIRED status added to existing PO status enum ────────
        migrations.AlterField(
            model_name='purchaseorder',
            name='status',
            field=models.CharField(
                max_length=22,
                choices=[
                    ('draft', 'Draft'),
                    ('pending_fm_approval', 'Pending FM Approval'),
                    ('pending_cfo_approval', 'Pending CFO Approval'),
                    ('rejected', 'Rejected'),
                    ('approved', 'Approved'),
                    ('partially_received', 'Partially Received'),
                    ('fully_received', 'Fully Received'),
                    ('closed', 'Closed'),
                    ('cancelled', 'Cancelled'),
                    ('expired', 'Expired (auto-closed)'),
                ],
                default='draft',
            ),
        ),
        # ── ApprovalDelegate ───────────────────────────────────────
        migrations.CreateModel(
            name='ApprovalDelegate',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role',       models.CharField(
                    max_length=20,
                    choices=[('fm','Finance Manager'),('cfo','CFO'),('tier1_manager','Tier-1 dept manager')],
                )),
                ('starts_at',  models.DateField()),
                ('ends_at',    models.DateField()),
                ('reason',     models.CharField(max_length=200, blank=True, default='')),
                ('is_active',  models.BooleanField(default=True)),
                ('delegator',  models.ForeignKey(
                    on_delete=models.CASCADE,
                    to=settings.AUTH_USER_MODEL,
                    related_name='approval_delegations_granted',
                )),
                ('delegate',   models.ForeignKey(
                    on_delete=models.PROTECT,
                    to=settings.AUTH_USER_MODEL,
                    related_name='approval_delegations_received',
                )),
            ],
            options={
                'verbose_name':        'Approval Delegate',
                'verbose_name_plural': 'Approval Delegates',
                'ordering':            ['-starts_at'],
            },
        ),
        # ── POAmendment ────────────────────────────────────────────
        migrations.CreateModel(
            name='POAmendment',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('version',    models.PositiveSmallIntegerField()),
                ('status',     models.CharField(
                    max_length=20,
                    default='draft',
                    choices=[('draft','Draft'),('pending_approval','Pending approval'),
                             ('approved','Approved'),('rejected','Rejected')],
                )),
                ('reason',     models.CharField(max_length=500, blank=True, default='')),
                ('diff',       models.JSONField(default=dict)),
                ('applied_at', models.DateTimeField(null=True, blank=True)),
                ('purchase_order', models.ForeignKey(
                    on_delete=models.PROTECT,
                    to='procurement.purchaseorder',
                    related_name='amendments',
                )),
                ('requested_by', models.ForeignKey(
                    on_delete=models.PROTECT,
                    to=settings.AUTH_USER_MODEL,
                    related_name='po_amendments_requested',
                )),
                ('approved_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                    related_name='po_amendments_approved',
                )),
            ],
            options={
                'verbose_name':     'PO Amendment',
                'ordering':         ['-created_at'],
                'unique_together':  {('purchase_order', 'version')},
            },
        ),
        # ── POAttachment ───────────────────────────────────────────
        migrations.CreateModel(
            name='POAttachment',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file',       models.FileField(upload_to=procurement.models._po_attachment_upload_to)),
                ('label',      models.CharField(max_length=120, blank=True, default='')),
                ('content_type', models.CharField(max_length=100, blank=True, default='')),
                ('size_bytes',   models.PositiveIntegerField(default=0)),
                ('purchase_order', models.ForeignKey(
                    on_delete=models.CASCADE,
                    to='procurement.purchaseorder',
                    related_name='attachments',
                )),
                ('uploaded_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                    related_name='po_attachments_uploaded',
                )),
            ],
            options={
                'verbose_name': 'PO Attachment',
                'ordering':     ['-created_at'],
            },
        ),
    ]
