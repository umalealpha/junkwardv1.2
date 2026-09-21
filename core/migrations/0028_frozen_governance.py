import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0027_chatmessage_edit_delete'),
    ]

    operations = [
        migrations.CreateModel(
            name='FrozenComponent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(choices=[
                    ('adic_ma_pl_layout', 'ADIC MA P&L layout'),
                    ('adic_revenue_mapping', 'ADIC revenue mapping'),
                    ('adic_gwp_figure', 'ADIC GWP figure'),
                ], max_length=40, unique=True)),
                ('label', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True, default='')),
                ('is_frozen', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Frozen Component',
                'verbose_name_plural': 'Frozen Components',
                'ordering': ['label'],
            },
        ),
        migrations.CreateModel(
            name='FrozenChangeRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('summary', models.CharField(max_length=200)),
                ('reason', models.TextField()),
                ('board_impact', models.TextField()),
                ('status', models.CharField(choices=[
                    ('pending', 'Pending CFO decision'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                ], db_index=True, default='pending', max_length=10)),
                ('decision_comment', models.TextField(blank=True, default='')),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('consumed', models.BooleanField(default=False)),
                ('component', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='change_requests', to='core.frozencomponent')),
                ('requested_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='frozen_change_requests_made', to=settings.AUTH_USER_MODEL)),
                ('decided_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='frozen_change_requests_decided', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Frozen Change Request',
                'verbose_name_plural': 'Frozen Change Requests',
                'ordering': ['-created_at'],
            },
        ),
    ]
