# FX-001 (CFO/Oprah directive 2026-05-28): rate maintenance — add
# loaded_by / approved_by / approved_at / notes to ExchangeRate so a
# Finance Manager must approve rates before any FX revaluation can use them.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_aria_conversation_message'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='exchangerate',
            name='loaded_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='fx_rates_loaded',
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='exchangerate',
            name='approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='fx_rates_approved',
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='exchangerate',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='exchangerate',
            name='notes',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='exchangerate',
            name='source',
            field=models.CharField(
                choices=[('manual', 'Manual Entry'), ('bank_of_botswana', 'Bank of Botswana')],
                default='bank_of_botswana', max_length=20),
        ),
    ]
