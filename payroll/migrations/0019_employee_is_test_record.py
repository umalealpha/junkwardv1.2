"""Mark the QA accounts as what they are, so staff reports stop counting them.

Oprah Mogomotsi's leave spot-check (2026-08-10) found "Manus Reviewer (automated
QA)" and "QA Test Subject (not a real person)" accruing annual leave beside real
employees on the Team Leave Report.

They cannot just be deleted — the eyes-on and QC harnesses drive real code paths
through real rows, which is the point of them. So they are flagged instead.

The flag is set here by matching the two names ONCE, as a data repair. The code
never matches on names: anything created later must set `is_test_record` itself.
"""
from django.db import migrations, models


#: Matched once, here only. Not a runtime rule.
KNOWN_TEST_NAMES = [
    'Manus Reviewer',
    'QA Test Subject',
]


def flag_the_known_test_rows(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    for fragment in KNOWN_TEST_NAMES:
        n = Employee.objects.filter(full_name__icontains=fragment).update(
            is_test_record=True)
        print(f'[payroll.0019] flagged {n} row(s) matching "{fragment}" as test records')


def unflag(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    Employee.objects.filter(is_test_record=True).update(is_test_record=False)


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0018_payroll_dual_signoff'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='is_test_record',
            field=models.BooleanField(
                default=False, db_index=True,
                help_text='A QA/automation account, not a real employee. Excluded '
                          'from staff reports.'),
        ),
        migrations.RunPython(flag_the_known_test_rows, unflag),
    ]
