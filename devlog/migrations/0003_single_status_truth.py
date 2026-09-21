from django.db import migrations, models


def live_dated_rows_are_live(apps, schema_editor):
    """Rows a release stamped live but a later call set back to open."""
    DevItem = apps.get_model('devlog', 'DevItem')
    DevItem.objects.filter(status__in=('asked', 'building', 'waiting'),
                           live_at__isnull=False).update(status='live')


class Migration(migrations.Migration):

    dependencies = [('devlog', '0002_requester_and_bug')]

    operations = [
        migrations.AddField(
            model_name='devitem', name='confirm_declined_at',
            field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(live_dated_rows_are_live, migrations.RunPython.noop),
    ]
