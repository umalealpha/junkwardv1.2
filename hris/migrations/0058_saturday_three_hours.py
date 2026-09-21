"""Re-stamp stored Saturday rows at the new 3h requirement — CFO 2026-08-01.

WorkdayJustification stores the required hours on the row when the day is
raised, so rows already written for 2026-08-01 and later Saturdays still carry
the old 4h. Saturdays BEFORE that date are left exactly as they were — they are
the record of what was asked of people at the time (the July audit relies on it).

Pinned to the policy date rather than date.today() so the outcome is identical
whenever the migration runs.
"""
from datetime import date
from decimal import Decimal

from django.db import migrations

SATURDAY = 5
NEW = Decimal('3.00')
EFFECTIVE_FROM = date(2026, 8, 1)   # the day the 3h rule takes effect


def restamp(apps, schema_editor):
    WorkdayJustification = apps.get_model('hris', 'WorkdayJustification')
    # Pinned to the POLICY date, not date.today(): the result must be the same
    # whenever this migration runs (a rebuilt database in September must not
    # re-stamp August rows, and a clock skew must not reach into July).
    rows = WorkdayJustification.objects.filter(work_date__gte=EFFECTIVE_FROM)
    ids = [r.id for r in rows
           if r.work_date.weekday() == SATURDAY and r.required_hours != NEW]
    if ids:
        WorkdayJustification.objects.filter(id__in=ids).update(required_hours=NEW)


class Migration(migrations.Migration):

    dependencies = [('hris', '0057_arun_dont_track')]

    operations = [migrations.RunPython(restamp, migrations.RunPython.noop)]
