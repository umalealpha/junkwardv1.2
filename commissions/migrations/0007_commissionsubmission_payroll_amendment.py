# Auto-feed approved payroll-group commissions into payroll (CFO 2026-08-28).
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('commissions', '0006_commissionsubmission_statement_file_and_more'),
        ('payroll', '0021_employee_archive'),
    ]
    operations = [
        migrations.AddField(
            model_name='commissionsubmission',
            name='payroll_amendment',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='commission_submissions',
                to='payroll.payrollamendment'),
        ),
    ]
