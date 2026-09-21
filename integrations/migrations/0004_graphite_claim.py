# Graphite V2 claims register (read-only mirror) — Bokani 2026-06-24.
# Hand-written to match integrations/models.py (no local makemigrations env).

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0003_timedoctor_daily_snapshot'),
    ]

    operations = [
        migrations.CreateModel(
            name='GraphiteClaim',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('graphite_id', models.BigIntegerField(db_index=True, unique=True)),
                ('claim_number', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('claim_type', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('status', models.CharField(blank=True, db_index=True, default='', max_length=32)),
                ('claim_handler', models.CharField(blank=True, default='', max_length=128)),
                ('customer_name', models.CharField(blank=True, default='', max_length=255)),
                ('customer_graphite_id', models.BigIntegerField(blank=True, null=True)),
                ('is_company', models.BooleanField(default=False)),
                ('policy_number', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('product_name', models.CharField(blank=True, default='', max_length=128)),
                ('registered_date', models.DateField(blank=True, db_index=True, null=True)),
                ('graphite_created_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Graphite Claim',
                'verbose_name_plural': 'Graphite Claims',
                'ordering': ['-registered_date', '-graphite_created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='graphiteclaim',
            index=models.Index(fields=['status', 'claim_type'], name='intg_gclaim_st_ty_idx'),
        ),
        migrations.AddIndex(
            model_name='graphiteclaim',
            index=models.Index(fields=['-registered_date'], name='intg_gclaim_regdate_idx'),
        ),
    ]
