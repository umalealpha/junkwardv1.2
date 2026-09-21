"""HR request 2026-07-01 (Unami): capture contract type (permanent / fixed-term /
probation / internship) and the probation end date on the employment contract —
the Conditions-of-Service structure. Additive; existing rows default to permanent."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0009_payslip_source_currency'),
    ]

    operations = [
        migrations.AddField(
            model_name='employmentcontract',
            name='contract_type',
            field=models.CharField(default='permanent', max_length=12, choices=[
                ('permanent', 'Permanent'), ('fixed_term', 'Fixed-term / duration'),
                ('probation', 'Probation'), ('internship', 'Internship / attachment')]),
        ),
        migrations.AddField(
            model_name='employmentcontract',
            name='probation_end_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
