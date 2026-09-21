# Auto-feed approved incentives into payroll (CFO 2026-08-28).
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0077_onboarding_control_upgrade'),
        ('payroll', '0021_employee_archive'),
    ]

    operations = [
        migrations.AddField(
            model_name='incentiveline',
            name='payroll_amendment',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='incentive_lines',
                to='payroll.payrollamendment',
            ),
        ),
    ]
