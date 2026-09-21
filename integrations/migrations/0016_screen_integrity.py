import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0015_claimstatuscode'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScreenIntegrityScan',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('day', models.DateField(db_index=True, help_text='Botswana calendar day scanned.', unique=True)),
                ('people_checked', models.PositiveIntegerField(default=0)),
                ('suspicious', models.PositiveIntegerField(default=0)),
                ('watch', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('ok', 'Ran'), ('no_data', 'No screenshots'), ('failed', 'Pull failed')], default='ok', max_length=8)),
                ('note', models.CharField(blank=True, default='', max_length=200)),
                ('ran_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Screen-integrity scan',
                'ordering': ['-day'],
            },
        ),
        migrations.CreateModel(
            name='ScreenIntegrityFlag',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('day', models.DateField(db_index=True)),
                ('td_user_id', models.CharField(blank=True, default='', max_length=64)),
                ('name', models.CharField(blank=True, default='', max_length=120)),
                ('suspicion', models.CharField(db_index=True, max_length=12)),
                ('shots', models.PositiveIntegerField(default=0)),
                ('frozen_typing_pct', models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ('frozen_typing_hours', models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ('mouse_dead_pct', models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ('identical_pct', models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ('reasons', models.JSONField(blank=True, default=list)),
                ('scan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='flags', to='integrations.screenintegrityscan')),
            ],
            options={
                'verbose_name': 'Screen-integrity flag',
                'ordering': ['-day', 'suspicion', '-frozen_typing_pct'],
            },
        ),
        migrations.AddIndex(
            model_name='screenintegrityflag',
            index=models.Index(fields=['day', 'suspicion'], name='integration_day_56f54d_idx'),
        ),
        migrations.AddConstraint(
            model_name='screenintegrityflag',
            constraint=models.UniqueConstraint(fields=['day', 'td_user_id'], name='uniq_screenflag_day_user'),
        ),
    ]
