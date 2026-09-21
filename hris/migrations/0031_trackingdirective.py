# Who-tracks override (CFO 2026-07-14): a per-employee track / don't-track flag
# the setup dashboard writes. ONLY TrackingDirective is created here — the
# monthlycheckin / PIP help-text AlterFields makemigrations also proposed are
# pre-existing drift, not part of this change.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0030_workdayjustification_review_note_and_more'),
        ('payroll', '0010_employmentcontract_type_probation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TrackingDirective',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('expected_to_track', models.BooleanField(default=True)),
                ('note', models.CharField(blank=True, default='', max_length=200)),
                ('employee', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='tracking_directive', to='payroll.employee')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Tracking Directive',
                'verbose_name_plural': 'Tracking Directives',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
    ]
