"""Cache DeepSeek-generated monthly 10-commandments (CFO 2026-05-18)."""
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_userprofile_hris_unlocked_until'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonthlyCommandments',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.CharField(
                    max_length=40,
                    help_text='Lowercase key — finance, claims, hr, it, etc.',
                )),
                ('year_month', models.CharField(
                    max_length=7,
                    help_text='YYYY-MM — month the row was generated for.',
                )),
                ('commandments', models.JSONField(
                    default=list,
                    help_text='List of {short, long} dicts. Length=10.',
                )),
                ('generated_at', models.DateTimeField(auto_now_add=True)),
                ('source', models.CharField(max_length=20, default='deepseek')),
            ],
            options={
                'verbose_name':        'Monthly Commandments',
                'verbose_name_plural': 'Monthly Commandments',
                'ordering':            ['-year_month', 'category'],
                'unique_together':     {('category', 'year_month')},
            },
        ),
    ]
