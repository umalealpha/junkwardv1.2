"""Take the CEO (Arun Iyer) off Time Doctor tracking — CFO 2026-08-01.

Writes the explicit TrackingDirective(expected_to_track=False) row that
hris.eligibility now honours, so Arun drops out of the daily brief, the
exception report, the Saturday explain chaser and the leave-excuse chaser.

Matched on email (aiyer@alphadirect.co.bw) with a full-name fallback. If no
employee row matches, the migration is a no-op — never fail a deploy over it.
"""
from django.db import migrations

EMAIL = 'aiyer@alphadirect.co.bw'
NAME = 'arun iyer'
NOTE = 'CFO 2026-08-01: CEO not tracked on Time Doctor.'


def dont_track_arun(apps, schema_editor):
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
        ('hris', '0056_okr_key_results_and_checkins'),
        ('payroll', '0001_initial'),
    ]

    operations = [migrations.RunPython(dont_track_arun, restore)]
