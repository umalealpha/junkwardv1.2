"""A bug report status outside the five choices makes the row un-clearable.

The board's open queue is `exclude(status__in=['resolved', 'wont_fix'])`, so any
other value is open forever — no filter matches it and no screen can clear it.
One row reached `status='closed'` (kbotana 2026-08-06, closed on the CFO's own
confirmation that the leave balances were right) and sat on the open board for
four days as a result.

The API rejects an unknown status already. A management shell or a data script
does not, because `choices` on a CharField is only enforced by `full_clean()`.
So: repair the stranded rows first, then add a database check that closes every
write path at once.
"""
from django.db import migrations, models


VALID = ['new', 'triaged', 'in_progress', 'resolved', 'wont_fix']


def land_stranded_statuses(apps, schema_editor):
    """Map anything outside the choices onto a real one.

    'closed' means the same thing the board calls 'resolved', and the row
    already carries the resolution note explaining the decision, so nothing is
    lost. Anything else unexpected goes to 'triaged' — visible and workable
    rather than silently resolved, because resolving a report nobody looked at
    would hide it from the very sweep that should pick it up.
    """
    BugReport = apps.get_model('core', 'BugReport')
    stranded = BugReport.objects.exclude(status__in=VALID)
    for row in stranded:
        row.status = 'resolved' if row.status == 'closed' else 'triaged'
        row.save(update_fields=['status'])


def noop_reverse(apps, schema_editor):
    """Nothing to undo — the old values were not valid states to return to."""


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_emaillogincode_purpose'),
    ]

    operations = [
        migrations.RunPython(land_stranded_statuses, noop_reverse),
        migrations.AddConstraint(
            model_name='bugreport',
            constraint=models.CheckConstraint(
                check=models.Q(status__in=VALID),
                name='bugreport_status_is_a_real_choice',
            ),
        ),
    ]
