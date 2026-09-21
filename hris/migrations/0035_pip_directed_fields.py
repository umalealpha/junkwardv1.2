from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0034_letterrequest_additional_details'),
    ]

    operations = [
        migrations.AddField(
            model_name='performanceimprovementplan',
            name='directed',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='performanceimprovementplan',
            name='title',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='performanceimprovementplan',
            name='employee_explanation',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='performanceimprovementplan',
            name='employee_explanation_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='performanceimprovementplan',
            name='viewer_emails',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
