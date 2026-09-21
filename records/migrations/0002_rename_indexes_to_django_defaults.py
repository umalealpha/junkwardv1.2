"""Rename the indexes to the names Django generates for itself.

0001 was hand-written (makemigrations could not run in the container at the time)
and gave the indexes readable names. Django derives its own hashed names from the
model, so `makemigrations --check` reported drift on every run — which would fail
the machine check on every future deploy for a purely cosmetic difference.

Renames only. No data, no schema change, no index dropped or rebuilt.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('records', '0001_initial'),
    ]

    operations = [
        migrations.RemoveIndex(model_name='recorditem', name='records_rec_status_idx'),
        migrations.RemoveIndex(model_name='recorditem', name='records_rec_holder_idx'),
        migrations.RemoveIndex(model_name='recorditem', name='records_rec_reten_idx'),
        migrations.RemoveIndex(model_name='recordmovement', name='records_mv_rec_idx'),
        migrations.RemoveIndex(model_name='recordmovement', name='records_mv_due_idx'),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['status'], name='records_rec_status_5d0021_idx'),
        ),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['current_holder'], name='records_rec_current_e8f65f_idx'),
        ),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['retention_until'], name='records_rec_retenti_874cda_idx'),
        ),
        migrations.AddIndex(
            model_name='recordmovement',
            index=models.Index(fields=['record', '-moved_at'], name='records_rec_record__26fab1_idx'),
        ),
        migrations.AddIndex(
            model_name='recordmovement',
            index=models.Index(fields=['due_back_on'], name='records_rec_due_bac_604e05_idx'),
        ),
    ]
