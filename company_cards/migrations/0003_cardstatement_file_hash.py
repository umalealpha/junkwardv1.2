"""Remember the bytes of each loaded statement file.

So that re-uploading the identical file can be recognised and queried before it
is processed a second time (Laone Thebe 2026-09-11). Blank on every statement
loaded before this field existed — an empty hash never matches another, so old
rows simply never trigger the warning.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('company_cards', '0002_cardspend_what_for_len'),
    ]

    operations = [
        migrations.AddField(
            model_name='cardstatement',
            name='file_hash',
            field=models.CharField(blank=True, db_index=True, default='',
                                   max_length=64),
        ),
    ]
