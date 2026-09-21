# applied_by audit field on PayrollAmendmentBatch (CFO 2026-07-23):
# staging-step SoD moved downstream; record who applied for the audit trail.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0012_encrypt_employee_pii"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="payrollamendmentbatch",
            name="applied_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payroll_amendment_batches_applied",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
