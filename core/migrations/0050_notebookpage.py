"""Shared CFO/Claude notebook page (CFO 2026-07-25).

Hand-written: makemigrations on this repo also emits unrelated pre-existing
help_text / index drift across core, payroll and hris.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0049_outboundemaillog'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotebookPage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('slug', models.SlugField(
                    default='main', max_length=60, unique=True,
                    help_text="Page name. 'main' is the one Claude reads at the "
                              "start of every session.")),
                ('title', models.CharField(blank=True, default='Notebook',
                                           max_length=140)),
                ('body', models.TextField(blank=True, default='',
                                          help_text='Plain text / Markdown. No secrets.')),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='notebook_edits',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Notebook page',
                'verbose_name_plural': 'Notebook pages',
                'ordering': ['slug'],
            },
        ),
    ]
