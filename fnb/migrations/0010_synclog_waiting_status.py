"""FNB sync log: 'waiting' is not 'failed'.

HTTP 425 Too Early is FNB answering "I have not processed that batch yet" -
the normal reply while a batch waits for authorisation at the bank. Every one
of them was stored as FAILED, so the seven days to 2026-09-12 showed 49,915
failures when the number of real, non-425 failures was ZERO. Dashboards that
cry wolf that loudly are worse than no dashboard: nobody can see a genuine
failure inside the noise.

Two steps:
  1. Widen the status choices to include 'waiting' (no SQL - Django stores
     choices in state only; the column is already a varchar).
  2. Reclassify the rows that are demonstrably a 425. Keyed on http_status,
     which is the bank's own answer, never on our error text. Nothing is
     deleted and no other status is touched.
"""
from django.db import migrations, models


def mark_425_as_waiting(apps, schema_editor):
    FNBSyncLog = apps.get_model('fnb', 'FNBSyncLog')
    n = (FNBSyncLog.objects
         .filter(status='failed', http_status=425)
         .update(status='waiting', error_message=''))
    if n:
        print(f'  [fnb synclog] {n} row(s) that were HTTP 425 "not processed yet" '
              f'are no longer counted as failures.')


def back_to_failed(apps, schema_editor):
    FNBSyncLog = apps.get_model('fnb', 'FNBSyncLog')
    FNBSyncLog.objects.filter(status='waiting').update(status='failed')


class Migration(migrations.Migration):

    dependencies = [('fnb', '0009_expresspayee_unique_account')]

    operations = [
        migrations.AlterField(
            model_name='fnbsynclog',
            name='status',
            field=models.CharField(
                choices=[('pending', 'Pending'), ('success', 'Success'),
                         ('failed', 'Failed'), ('timeout', 'Timed out'),
                         ('skipped', 'Skipped (not configured)'),
                         ('waiting', 'Waiting — the bank has not processed it yet')],
                default='pending', max_length=10),
        ),
        migrations.RunPython(mark_425_as_waiting, back_to_failed),
    ]
