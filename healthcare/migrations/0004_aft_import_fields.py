# Generated 2026-06-13 — AFT weekly claims importer fields (CFO/Tlamelo).
# Adds source_hash / remit_date / superseded / ai_summary to HealthcareUpload.
# Hand-authored to match the model; deploy entrypoint applies it.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('healthcare', '0003_uploads'),
    ]

    operations = [
        migrations.AddField(
            model_name='healthcareupload',
            name='source_hash',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='healthcareupload',
            name='remit_date',
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='healthcareupload',
            name='superseded',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='healthcareupload',
            name='ai_summary',
            field=models.TextField(blank=True, default=''),
        ),
    ]
