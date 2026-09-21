"""Claims PO async progress + timing (CFO 2026-07-08): instant upload, live
progress bar + ETA, self-learning timing."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0009_discount_column_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='claimsassessment',
            name='progress_stage',
            field=models.CharField(
                max_length=12, default='uploaded', db_index=True,
                choices=[('uploaded', 'Uploaded'), ('parsing', 'Reading the PDF'),
                         ('generating', 'Creating purchase orders'),
                         ('done', 'Done'), ('failed', 'Failed')]),
        ),
        migrations.AddField(
            model_name='claimsassessment',
            name='progress_pct',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(model_name='claimsassessment', name='parse_ms',
                            field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='claimsassessment', name='generate_ms',
                            field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='claimsassessment', name='total_ms',
                            field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='claimsassessment', name='processing_started_at',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='claimsassessment', name='processing_finished_at',
                            field=models.DateTimeField(blank=True, null=True)),
    ]
