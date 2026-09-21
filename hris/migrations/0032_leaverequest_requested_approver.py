"""Add LeaveRequest.requested_approver — who the employee asked to review.

CFO directive 2026-07-15: employees pick who reviews their leave request
(restricted to genuine people-managers), instead of it always routing to
HRISProfile.manager. Distinct from `approver` (who actually decided).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hris', '0031_trackingdirective'),
    ]

    operations = [
        migrations.AddField(
            model_name='leaverequest',
            name='requested_approver',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='leave_requests_to_review',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
