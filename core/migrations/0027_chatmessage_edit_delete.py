from django.db import migrations, models


class Migration(migrations.Migration):
    """Bug e79e4166: edit/delete for team-chat messages.
    Two additive, safe columns (nullable / default) — no data migration."""

    dependencies = [
        ('core', '0026_bugreport_triage_queue'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='edited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='is_deleted',
            field=models.BooleanField(default=False),
        ),
    ]
