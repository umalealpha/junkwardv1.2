"""Outbound email oversight log (CFO instruction 2026-07-25, option A).

Hand-written on purpose. `makemigrations` on this repo also emits pre-existing
help_text / index drift across core, payroll and hris that has nothing to do
with this change; this migration is deliberately limited to the new table.
"""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0048_compliancebrainsummary'),
    ]

    operations = [
        migrations.CreateModel(
            name='OutboundEmailLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('subject', models.CharField(blank=True, default='', max_length=255)),
                ('from_email', models.CharField(blank=True, default='', max_length=255)),
                ('to_addrs', models.TextField(blank=True, default='')),
                ('cc_addrs', models.TextField(blank=True, default='')),
                ('bcc_addrs', models.TextField(blank=True, default='')),
                ('blocked_addrs', models.TextField(
                    blank=True, default='',
                    help_text='Addresses stripped because they are on '
                              'NEVER_DELIVER_EMAILS.')),
                ('attachment_count', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(
                    choices=[('sent', 'Sent'),
                             ('blocked', 'Blocked (all recipients barred)'),
                             ('failed', 'Failed (send error)')],
                    default='sent', max_length=10)),
            ],
            options={
                'verbose_name': 'Outbound Email Log',
                'verbose_name_plural': 'Outbound Email Log',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='outboundemaillog',
            index=models.Index(fields=['-created_at'],
                               name='core_outbou_created_1e8f2a_idx'),
        ),
        migrations.AddIndex(
            model_name='outboundemaillog',
            index=models.Index(fields=['status'],
                               name='core_outbou_status_7c31b9_idx'),
        ),
    ]
