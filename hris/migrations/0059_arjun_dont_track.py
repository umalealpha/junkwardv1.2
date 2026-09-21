"""Take Arjun (COO) off Time Doctor tracking — CFO 2026-08-01.

Writes the explicit TrackingDirective(expected_to_track=False) row that
hris.eligibility honours, so he stops receiving his own morning Time Doctor
brief and stops appearing in the reports.

Matched on EMAIL ONLY. His omni employee record is misnamed "Arjun
Parameswaran" (a known duplicate/mislabelled row — see SAT_OFF_EMAILS in
send_saturday_explain), so a full-name fallback would either miss him or catch
the wrong person. No match → no-op; never fail a deploy over it.
"""
from django.db import migrations

EMAIL = 'arjuniyer@alphadirect.co.bw'
NOTE = 'CFO 2026-08-01: COO not tracked on Time Doctor.'


def dont_track_arjun(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    TrackingDirective = apps.get_model('hris', 'TrackingDirective')
    for emp in Employee.objects.filter(email__iexact=EMAIL):
        TrackingDirective.objects.update_or_create(
            employee=emp, defaults={'expected_to_track': False, 'note': NOTE})


def restore(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    TrackingDirective = apps.get_model('hris', 'TrackingDirective')
    for emp in Employee.objects.filter(email__iexact=EMAIL):
        TrackingDirective.objects.filter(employee=emp, note=NOTE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0058_saturday_three_hours'),
        ('payroll', '0001_initial'),
    ]

    operations = [migrations.RunPython(dont_track_arjun, restore)]
