from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0038_userprofile_job_title'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['table_name', 'record_id'], name='auditlog_table_record_idx'),
        ),
    ]
