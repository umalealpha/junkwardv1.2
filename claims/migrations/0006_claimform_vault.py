# Hand-authored: Claim Forms Vault (CFO 2026-08-12). Additive, new table only.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('claims', '0005_backfill_salvage_into_salvage_app'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ClaimForm',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('slug', models.CharField(db_index=True, max_length=80, unique=True)),
                ('title', models.CharField(max_length=200)),
                ('category', models.CharField(
                    choices=[
                        ('motor', 'Motor'),
                        ('property', 'Property & assets'),
                        ('liability', 'Liability'),
                        ('engineering', 'Engineering'),
                        ('marine', 'Marine'),
                        ('life_health', 'Life & health'),
                        ('specialty', 'Specialty'),
                        ('other', 'Other'),
                    ],
                    db_index=True, default='other', max_length=20)),
                ('file', models.FileField(upload_to='claim_forms/')),
                ('source_name', models.CharField(blank=True, default='', max_length=255)),
                ('description', models.TextField(blank=True, default='')),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('sort_order', models.PositiveIntegerField(default=100)),
                ('uploaded_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='claim_forms_uploaded',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'claim form',
                'verbose_name_plural': 'claim forms',
                'ordering': ['sort_order', 'title'],
            },
        ),
    ]
