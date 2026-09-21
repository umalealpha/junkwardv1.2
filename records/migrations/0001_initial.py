import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0002_company'),
        ('payroll', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RecordCategory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120, unique=True)),
                ('description', models.TextField(blank=True, default='')),
                ('retention_months', models.PositiveIntegerField(blank=True, null=True)),
                ('active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name_plural': 'record categories',
                'ordering': ['name'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RecordItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(
                    help_text='The reference written on the file itself.',
                    max_length=60, unique=True)),
                ('title', models.CharField(max_length=250)),
                ('confidentiality', models.CharField(
                    choices=[('public', 'Public'), ('internal', 'Internal'),
                             ('confidential', 'Confidential'),
                             ('restricted', 'Restricted — personal data')],
                    default='internal', max_length=20)),
                ('status', models.CharField(
                    choices=[('in_store', 'In the storeroom'), ('issued', 'Issued out'),
                             ('archived', 'Archived off-site'), ('destroyed', 'Destroyed'),
                             ('lost', 'Missing')],
                    default='in_store', max_length=20)),
                ('current_custodian', models.CharField(blank=True, default='', max_length=200)),
                ('current_location', models.CharField(blank=True, default='', max_length=200)),
                ('opened_on', models.DateField(blank=True, null=True)),
                ('closed_on', models.DateField(blank=True, null=True)),
                ('retention_until', models.DateField(blank=True, null=True)),
                ('legal_hold', models.BooleanField(default=False)),
                ('legal_hold_note', models.CharField(blank=True, default='', max_length=300)),
                ('notes', models.TextField(blank=True, default='')),
                ('category', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='items', to='records.recordcategory')),
                ('company', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='record_items', to='core.company')),
                ('current_holder', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='records_held', to='payroll.employee')),
            ],
            options={
                'ordering': ['reference'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RecordMovement',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(
                    choices=[('issue', 'Issued out'), ('return', 'Returned to store'),
                             ('transfer', 'Passed to someone else'),
                             ('archive', 'Sent to archive'), ('destroy', 'Destroyed')],
                    max_length=20)),
                ('from_custodian', models.CharField(blank=True, default='', max_length=200)),
                ('to_custodian', models.CharField(blank=True, default='', max_length=200)),
                ('from_location', models.CharField(blank=True, default='', max_length=200)),
                ('to_location', models.CharField(blank=True, default='', max_length=200)),
                ('reason', models.CharField(blank=True, default='', max_length=300)),
                ('moved_at', models.DateField()),
                ('due_back_on', models.DateField(blank=True, null=True)),
                ('from_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='records_given', to='payroll.employee')),
                ('to_employee', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='records_received', to='payroll.employee')),
                ('record', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='movements', to='records.recorditem')),
                ('recorded_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='record_movements', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-moved_at', '-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['status'], name='records_rec_status_idx'),
        ),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['current_holder'], name='records_rec_holder_idx'),
        ),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['retention_until'], name='records_rec_reten_idx'),
        ),
        migrations.AddIndex(
            model_name='recordmovement',
            index=models.Index(fields=['record', '-moved_at'], name='records_mv_rec_idx'),
        ),
        migrations.AddIndex(
            model_name='recordmovement',
            index=models.Index(fields=['due_back_on'], name='records_mv_due_idx'),
        ),
    ]
