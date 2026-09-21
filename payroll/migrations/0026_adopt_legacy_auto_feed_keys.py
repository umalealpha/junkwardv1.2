"""Give the rows the incentive and commission feeds already wrote a feed_key.

Both feeds used to match on the field tuple (batch, employee, kind, component)
and left `feed_key` NULL. From 16-Sep-2026 they match on `feed_key`, which the
database holds unique. Without this step the first run after the change would
not RECOGNISE its own earlier rows: it would look for the fingerprint, find
nothing, and write a SECOND amendment beside the old one for the same person,
same month, same payslip line.

So the fingerprint is stamped onto the rows that are already there, using the
identical `make_feed_key` the new code computes. After this, the first run
UPDATES what it wrote before, exactly as a re-run always did.

Only automatic rows are touched — the two feed markers, and only where the key
is still NULL. Hand-keyed amendments keep `feed_key = NULL` and are untouched.
If two legacy rows somehow fingerprint the same (a duplicate that pre-dates the
unique column), the first keeps the key and the rest stay NULL rather than
blowing up the deploy: a leftover duplicate is visible on the batch, a failed
migration is a crash-looping container.
"""
from django.db import migrations

MARKERS = ('AUTO-INCENTIVE', 'AUTO-COMMISSION')


def adopt(apps, schema_editor):
    from payroll.feed_common import make_feed_key

    PayrollAmendment = apps.get_model('payroll', 'PayrollAmendment')
    taken = set(
        PayrollAmendment.objects.filter(feed_key__isnull=False)
        .values_list('feed_key', flat=True)
    )

    rows = (PayrollAmendment.objects
            .filter(feed_key__isnull=True,
                    batch__file_name__in=MARKERS,
                    employee__isnull=False,
                    component__isnull=False)
            .select_related('batch', 'batch__target_period', 'component'))

    updated = []
    for amd in rows:
        period = getattr(amd.batch.target_period, 'period_name', '') or ''
        key = make_feed_key(amd.batch.file_name, period, amd.batch.company_id,
                            amd.employee_id, amd.component.code or '')
        if key in taken:
            continue
        taken.add(key)
        amd.feed_key = key
        updated.append(amd)

    if updated:
        PayrollAmendment.objects.bulk_update(updated, ['feed_key'], batch_size=500)


def unadopt(apps, schema_editor):
    """Reverse: hand the automatic rows back their NULL key."""
    PayrollAmendment = apps.get_model('payroll', 'PayrollAmendment')
    (PayrollAmendment.objects
     .filter(feed_key__isnull=False, batch__file_name__in=MARKERS)
     .update(feed_key=None))


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0025_salaryadvancepayout'),
    ]

    operations = [
        migrations.RunPython(adopt, unadopt),
    ]
