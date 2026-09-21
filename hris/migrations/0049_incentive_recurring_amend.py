# Hand-written (CFO 2026-07-22). Adds the RecurringIncentive template table and
# the three new IncentiveRequest columns the amend + recurring features need.
# Deliberately surgical: makemigrations would also emit unrelated pre-existing
# help_text/index drift across core/payroll/hris — that drift is NOT included
# here (surgical-change rule, mirrors 0043_leaveencashment).
#
# RecurringIncentive is intentionally auth-only (employee stored as a plain
# UUIDField, not a payroll FK) so this migration needs no cross-app dependency —
# only 0048 for linear hris history + the swappable AUTH_USER_MODEL.

import core.models
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0048_disciplinaryattachment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RecurringIncentive',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(help_text='Person or category, mirrors IncentiveLine.name.', max_length=160)),
                ('category', models.CharField(blank=True, default='', help_text='Grouping label used in the generated request title, e.g. "Motor claims incentive".', max_length=120)),
                ('basis', models.CharField(blank=True, default='', max_length=200)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('employee_id', models.UUIDField(blank=True, null=True)),
                ('department', models.CharField(blank=True, default='', max_length=80)),
                ('justification', models.TextField(blank=True, default='')),
                ('note', models.TextField(blank=True, default='')),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('created_by_email', models.CharField(blank=True, default='', max_length=254)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Recurring Incentive',
                'verbose_name_plural': 'Recurring Incentives',
                'ordering': ['name'],
                'abstract': False,
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
        migrations.AddField(
            model_name='incentiverequest',
            name='amended_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='incentiverequest',
            name='amended_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='incentiverequest',
            name='source_template',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generated_requests', to='hris.recurringincentive'),
        ),
    ]
