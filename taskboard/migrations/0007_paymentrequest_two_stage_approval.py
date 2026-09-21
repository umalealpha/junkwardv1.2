# Two-stage payment-request authorisation (CFO 2026-07-23):
# finance sign-off (Pako / Kago / Legakwa) BEFORE the CFO sees it.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("taskboard", "0006_drop_dwell_validator"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending_finance", "Pending finance sign-off"),
                    ("pending_cfo", "Pending CFO authorisation"),
                    ("rejected", "Rejected at finance sign-off"),
                ],
                default="pending_finance",
                db_index=True,
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="paymentrequest",
            name="first_approver",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payment_requests_first_approved",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="paymentrequest",
            name="first_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentrequest",
            name="rejected_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payment_requests_rejected",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="paymentrequest",
            name="rejected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentrequest",
            name="decision_notes",
            field=models.TextField(blank=True, default=""),
        ),
    ]
