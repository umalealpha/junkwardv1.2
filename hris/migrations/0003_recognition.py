"""
0003_recognition — peer kudos model.

CFO directive 2026-05-20 (Manus HRIS audit §5).
"""

import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0002_leave_medical_certificate'),
    ]

    operations = [
        migrations.CreateModel(
            name='Recognition',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('value_demonstrated', models.CharField(
                    max_length=20,
                    choices=[
                        ('integrity',  'Integrity'),
                        ('excellence', 'Excellence'),
                        ('ownership',  'Ownership'),
                        ('teamwork',   'Teamwork'),
                        ('innovation', 'Innovation'),
                        ('customer',   'Customer Focus'),
                    ],
                )),
                ('message', models.CharField(
                    max_length=500,
                    help_text='≤ 500 chars. Shown verbatim on the kudos feed.',
                )),
                ('points', models.PositiveSmallIntegerField(
                    default=1,
                    help_text='1–5. Higher values flagged for HR review (advisory only).',
                )),
                ('is_public', models.BooleanField(
                    default=True,
                    help_text='Private kudos visible only to receiver + their manager.',
                )),
                ('sender', models.ForeignKey(
                    on_delete=models.CASCADE,
                    related_name='kudos_sent',
                    to='hris.hrisprofile',
                )),
                ('receiver', models.ForeignKey(
                    on_delete=models.CASCADE,
                    related_name='kudos_received',
                    to='hris.hrisprofile',
                )),
            ],
            options={
                'verbose_name':        'Recognition (Kudos)',
                'verbose_name_plural': 'Recognition (Kudos)',
                'ordering':            ['-created_at'],
            },
        ),
    ]
