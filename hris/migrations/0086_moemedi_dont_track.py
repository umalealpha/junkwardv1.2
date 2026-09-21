"""Mark Moemedi Mositiemang as don't-track — he is a driver, no Time Doctor.
CFO directive 2026-09-10.
"""
from django.db import migrations

EMAIL = 'mmositiemang@alphadirect.co.bw'
NAME = 'moemedi mositiemang'
NOTE = 'CFO 2026-09-10: driver, not tracked on Time Doctor.'


def dont_track(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    TrackingDirective = apps.get_model('hris', 'TrackingDirective')
    emp = Employee.objects.filter(email__iexact=EMAIL).first()
    if emp is None:
        emp = Employee.objects.filter(full_name__iexact=NAME).first()
    if emp is None:
        return
    TrackingDirective.objects.update_or_create(
        employee=emp, defaults={'expected_to_track': False, 'note': NOTE})


def restore(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    TrackingDirective = apps.get_model('hris', 'TrackingDirective')
    emp = Employee.objects.filter(email__iexact=EMAIL).first()
    if emp is not None:
        TrackingDirective.objects.filter(employee=emp, note=NOTE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0085_feedback_reminder2'),
        ('payroll', '0001_initial'),
    ]

    operations = [migrations.RunPython(dont_track, restore)]
