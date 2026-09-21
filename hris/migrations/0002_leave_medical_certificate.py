"""Add LeaveRequest.medical_certificate (CoS §7.6.1 — CFO 2026-05-18)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='leaverequest',
            name='medical_certificate',
            field=models.FileField(
                upload_to='leave_certs/',
                null=True, blank=True,
                help_text='Required for Sick Leave (CoS §7.6.1). Max 5 MB.',
            ),
        ),
    ]
