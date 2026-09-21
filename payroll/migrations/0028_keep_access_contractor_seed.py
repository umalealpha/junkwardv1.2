"""Tick "keep access after exit" for the one live contractor, in the same
deploy that introduces the automatic leaver close-off.

CFO 2026-09-18. Chipo Bamusi's employment ended on 21 Aug 2026 and she now
works with us as an external contractor, so she legitimately keeps her Omni
login and her mailbox. She is the ONLY person in that position today —
confirmed against prod: of the fourteen employees carrying a past termination
date, seven still had an active login and she is the only one who should.

This is a data migration rather than a note telling somebody to tick a box,
because the gap between "the automation went live" and "somebody remembered to
tick the box" is exactly long enough to cut off a working contractor. The
nightly sweep would have closed her at 23:30 on the day of the deploy.

Idempotent and safe on any database: if the row is not there (a fresh test
database, a local dev copy) it simply does nothing.
"""
from django.db import migrations

CONTRACTOR_EMAIL = 'cbamusi@insurance.co.bw'


def keep_contractor_access(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    Employee.objects.filter(email__iexact=CONTRACTOR_EMAIL).update(
        keep_access_after_exit=True)


def undo(apps, schema_editor):
    Employee = apps.get_model('payroll', 'Employee')
    Employee.objects.filter(email__iexact=CONTRACTOR_EMAIL).update(
        keep_access_after_exit=False)


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0027_employee_keep_access_after_exit'),
    ]

    operations = [
        migrations.RunPython(keep_contractor_access, undo),
    ]
