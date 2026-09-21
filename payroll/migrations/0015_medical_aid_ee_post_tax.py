# Medical Aid employee contribution is POST-TAX, not pre-tax (Kago/Legakwa
# 2026-07-23). Botswana: employee medical-aid contributions are NOT deductible
# for PAYE (unlike approved pension / provident, which stay employee_pretax).
# The validated June run computed PAYE without deducting medical aid; this
# aligns the component's tax classification with that. Reversible.

from django.db import migrations


def _to_post_tax(apps, schema_editor):
    C = apps.get_model('payroll', 'PayslipComponent')
    C.objects.filter(code='MEDICAL_AID_EE', kind='employee_pretax').update(
        kind='employee_deduction')


def _to_pretax(apps, schema_editor):
    C = apps.get_model('payroll', 'PayslipComponent')
    C.objects.filter(code='MEDICAL_AID_EE', kind='employee_deduction').update(
        kind='employee_pretax')


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0014_employee_qualifications"),
    ]

    operations = [
        migrations.RunPython(_to_post_tax, _to_pretax),
    ]
