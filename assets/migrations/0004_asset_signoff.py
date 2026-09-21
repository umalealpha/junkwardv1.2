# Hand-authored — adds the AssetSignOff table.
#
# State-only since 2026-06-05 audit: 0001_initial was regenerated and now
# creates the AssetSignOff table and its indexes, so this CreateModel collided
# ("table assets_assetsignoff already exists") on fresh-DB / test / DR builds.
# Production already has 0004 recorded as applied. Wrapped in
# SeparateDatabaseAndState to preserve migration state while emitting no SQL.
# See assets/migrations/0002 for the same fix and full rationale.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0003_dual_authorization'),
        ('core', '0003_userprofile_title_isadmin'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        migrations.CreateModel(
            name='AssetSignOff',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(
                    choices=[
                        ('fully_depreciated_review', 'Fully-depreciated asset still in use'),
                        ('semi_annual_count', 'Semi-annual asset count'),
                    ],
                    max_length=30,
                )),
                ('period_label', models.CharField(max_length=40)),
                ('due_date', models.DateField()),
                ('first_signed_at', models.DateTimeField(blank=True, null=True)),
                ('first_role_label', models.CharField(blank=True, default='Manager', max_length=80)),
                ('second_signed_at', models.DateTimeField(blank=True, null=True)),
                ('second_role_label', models.CharField(blank=True, default='Finance Manager', max_length=80)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('partially_signed', 'Partially signed'),
                        ('completed', 'Completed'),
                        ('overdue', 'Overdue'),
                        ('cancelled', 'Cancelled'),
                    ],
                    default='pending', max_length=20,
                )),
                ('counted_assets', models.PositiveIntegerField(default=0)),
                ('discrepancies_text', models.TextField(blank=True, default='')),
                ('notes', models.TextField(blank=True, default='')),
                ('last_reminder_at', models.DateTimeField(blank=True, null=True)),
                ('asset', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='signoffs', to='assets.asset',
                )),
                ('company', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='asset_signoffs', to='core.company',
                )),
                ('first_signed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='asset_signoffs_first', to=settings.AUTH_USER_MODEL,
                )),
                ('second_signed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='asset_signoffs_second', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Asset Sign-Off',
                'verbose_name_plural': 'Asset Sign-Offs',
                'ordering': ['due_date', '-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='assetsignoff',
            index=models.Index(fields=['kind', 'status'], name='assets_aso_kind_status_idx'),
        ),
        migrations.AddIndex(
            model_name='assetsignoff',
            index=models.Index(fields=['due_date'], name='assets_aso_due_idx'),
        ),
        ]),
    ]
