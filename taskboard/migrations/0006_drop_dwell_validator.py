# Dwell gate removed (CFO 2026-07-18): interaction_seconds is audit-only now,
# so the MinValueValidator(30) drops to 0. Validator-only change — no schema op.
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("taskboard", "0005_paymentrequest_category_paymentrequestattachment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="completionnote",
            name="interaction_seconds",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Measured dwell time on the completion modal (kept for audit).",
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]
