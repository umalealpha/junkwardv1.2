"""
One leave opening balance per (employee, leave type, as-at date).

Bug c2888ba7 — HR: "OMNI accepts the report, but does not update the balances." A corrective
upload wrote a SECOND row for the same key instead of replacing the first. The balance query
ordered only by as_at_date, so with two rows sharing that date Postgres returned them in
arbitrary order and the OLD figure could win. 63 keys were colliding on production.

This migration removes the duplicates, keeping the most recently uploaded row for each key —
which is the correction HR intended — and then adds the constraint that stops it recurring.
The constraint is also what makes `update_or_create` safe under two concurrent uploads.
"""
from django.db import migrations, models


def drop_duplicate_openings(apps, schema_editor):
    """Keep the newest row per (profile, leave_type_code, as_at_date); delete the rest."""
    LeaveOpeningBalance = apps.get_model('hris', 'LeaveOpeningBalance')
    seen, doomed = set(), []
    # Newest first, so the first row seen for a key is the one to keep.
    for row in (LeaveOpeningBalance.objects
                .order_by('profile_id', 'leave_type_code', '-as_at_date', '-created_at')
                .only('id', 'profile_id', 'leave_type_code', 'as_at_date')):
        key = (row.profile_id, row.leave_type_code, row.as_at_date)
        if key in seen:
            doomed.append(row.id)
        else:
            seen.add(key)
    if doomed:
        LeaveOpeningBalance.objects.filter(id__in=doomed).delete()
    print(f'[hris.0061] kept {len(seen)} opening balance(s), removed {len(doomed)} duplicate(s)')


def noop(apps, schema_editor):
    """Deleted duplicates are not recoverable — the constraint is simply dropped on reverse."""


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0060_hrdocument_job_description_category'),
    ]

    operations = [
        migrations.RunPython(drop_duplicate_openings, noop),
        migrations.AddConstraint(
            model_name='leaveopeningbalance',
            constraint=models.UniqueConstraint(
                fields=['profile', 'leave_type_code', 'as_at_date'],
                name='hris_uniq_leave_opening_key'),
        ),
    ]
