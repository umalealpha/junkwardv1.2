"""Put due_back_on on the record itself.

Fable review: the overdue list joined every historical movement, so a file that
had once been returned late stayed overdue for ever — and the badge counted it.
Overdue is a fact about where a record is NOW, so it belongs on the record.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('records', '0002_rename_indexes_to_django_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='recorditem',
            name='due_back_on',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='recorditem',
            index=models.Index(fields=['due_back_on'],
                               name='records_rec_due_bac_9d3f21_idx'),
        ),
    ]
