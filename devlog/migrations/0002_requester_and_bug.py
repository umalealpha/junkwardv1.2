import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Who asked, and the bug it came from (CFO 2026-09-09)."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0068_calendar_invite_log'),
        ('devlog', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='devitem', name='requested_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dev_requests', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='devitem', name='requested_by_name',
            field=models.CharField(blank=True, db_index=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='devitem', name='bug',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='dev_items', to='core.bugreport'),
        ),
    ]
