from django.db import migrations, models


def backfill(apps, schema_editor):
    DD = apps.get_model('hris', 'DevelopmentDialogue')
    for row in DD.objects.all():
        row.person_key = row.email or row.ref
        row.is_current = True
        row.save(update_fields=['person_key', 'is_current'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0037_developmentdialogue_email'),
    ]

    operations = [
        migrations.AlterField(
            model_name='developmentdialogue',
            name='ref',
            field=models.CharField(db_index=True, max_length=80, unique=True),
        ),
        migrations.AddField(
            model_name='developmentdialogue',
            name='person_key',
            field=models.CharField(blank=True, db_index=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='developmentdialogue',
            name='is_current',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.RunPython(backfill, noop),
    ]
