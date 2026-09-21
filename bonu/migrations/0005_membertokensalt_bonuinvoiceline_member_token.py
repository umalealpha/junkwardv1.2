# Hand-written to match bonu/models.py (no container engine on the build machine).
# MUST be checked with `makemigrations bonu --check --dry-run` before deploy — expect
# "No changes detected".

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bonu', '0004_queryletter_approvalthreshold_ingesteddocument'),
    ]

    operations = [
        migrations.CreateModel(
            name='MemberTokenSalt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('salt', models.CharField(max_length=128)),
            ],
            options={
                'verbose_name': 'BONU member token salt',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='bonuinvoiceline',
            name='member_token',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=20,
                help_text='One-way token of the member, for spotting the same person across '
                          'firms. Cannot be turned back into a name.'),
        ),
    ]
