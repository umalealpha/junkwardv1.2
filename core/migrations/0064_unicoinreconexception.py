import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0063_userprofile_assignee_home_department'),
    ]

    operations = [
        migrations.CreateModel(
            name='UniCoinReconException',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('policy_number', models.CharField(db_index=True, max_length=64)),
                ('kind', models.CharField(
                    choices=[
                        ('multiple_gates', 'Multiple pay gates on one policy'),
                        ('collecting_cancelled', 'Still collecting on a cancelled policy'),
                        ('collecting_inactive', 'Collecting on an inactive policy'),
                        ('debit_past_plan', 'Debit beyond the agreed instalment plan'),
                        ('deactivated_paying', 'Deactivated policy still paying'),
                        ('not_posted', 'Collected but not posted to Graphite'),
                        ('other', 'Other'),
                    ],
                    db_index=True, default='other', max_length=24)),
                ('period', models.CharField(
                    blank=True, default='', max_length=16,
                    help_text="Reporting period, e.g. '2026-09'. Part of the dedupe key.")),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ('currency', models.CharField(default='BWP', max_length=3)),
                ('reason', models.TextField(
                    help_text='Plain-language finding — identifiers + figures only, no client names.')),
                ('recommended_action', models.TextField(blank=True, default='')),
                ('source_ref', models.CharField(
                    blank=True, default='', max_length=120,
                    help_text="The detection flow's own reference for this finding.")),
                ('status', models.CharField(
                    choices=[('open', 'Open'), ('cleared', 'Cleared'),
                             ('dismissed', 'Dismissed — not a real exception')],
                    db_index=True, default='open', max_length=10)),
                ('cleared_at', models.DateTimeField(blank=True, null=True)),
                ('cleared_note', models.TextField(blank=True, default='')),
                ('cleared_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='unicoin_recon_cleared', to=settings.AUTH_USER_MODEL)),
                ('owner', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='unicoin_recon_owned', to=settings.AUTH_USER_MODEL)),
                ('raised_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    help_text='Service account or staff user that raised it.',
                    related_name='unicoin_recon_raised', to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='unicoin_recon_exceptions', to='core.omnitask')),
            ],
            options={
                'verbose_name': 'UniCoin reconciliation exception',
                'verbose_name_plural': 'UniCoin reconciliation exceptions',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='unicoinreconexception',
            constraint=models.UniqueConstraint(
                fields=('policy_number', 'kind', 'period'),
                name='uniq_unicoin_recon_policy_kind_period'),
        ),
        migrations.AddIndex(
            model_name='unicoinreconexception',
            index=models.Index(fields=['status', 'kind'], name='core_unicoi_status_kind_idx'),
        ),
    ]
