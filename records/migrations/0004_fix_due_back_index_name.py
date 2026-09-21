"""Rename the due_back_on index to the name Django derives.

Second time in this app: 0002 fixed the same class of drift for the 0001 indexes,
and 0003 then hand-picked another name and drifted again. The lesson is not to
guess Django's hashed index names — take them from `makemigrations --check`.
Rename only; no data, no schema change.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('records', '0003_recorditem_due_back_on'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='recorditem', name='records_rec_due_bac_9d3f21_idx'),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['due_back_on'],
                               name='records_rec_due_bac_900077_idx'),
        ),
    ]
