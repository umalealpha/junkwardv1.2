"""Hand-written (NOT makemigrations) to add ONLY the payroll_period FK.

The core/payroll/hris apps carry pre-existing help_text/index model-state
drift, so a bare `makemigrations` would sweep unrelated churn into this file.
Adding just the one field keeps the migration surgical (same approach as the
leave-encashment 0043 migration).
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('fnb', '0003_fnbbatchsubmission_payments_and_more'),
        ('payroll', '0015_medical_aid_ee_post_tax'),
    ]

    operations = [
        migrations.AddField(
            model_name='fnbbatchsubmission',
            name='payroll_period',
            field=models.ForeignKey(
                null=True, blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='fnb_batches',
                to='payroll.payrollperiod',
            ),
        ),
    ]
