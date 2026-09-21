# Hand-written (CFO 2026-07-25). Adds the ManagerAccountabilityNote table for
# the manager-accountability feature. Surgical: only this table, no unrelated
# makemigrations drift (mirrors 0049 / 0043).
import core.models
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0049_incentive_recurring_amend'),
        ('payroll', '0012_encrypt_employee_pii'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManagerAccountabilityNote',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('for_date', models.DateField(db_index=True)),
                ('reports', models.JSONField(default=list)),
                ('deadline', models.DateTimeField()),
                ('response', models.TextField(blank=True, default='')),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('escalated_at', models.DateTimeField(blank=True, null=True)),
                ('manager', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='accountability_notes', to='payroll.employee')),
            ],
            options={
                'verbose_name': 'Manager accountability note',
                'ordering': ['-for_date'],
                'unique_together': {('manager', 'for_date')},
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
    ]
