# Hand-written to ONLY add the new LeaveExcuseAutoResponse table (CFO directive
# 2026-07-22 — Leave Excuse Response dashboard). makemigrations also emits
# unrelated help_text/index drift on core/payroll/hris that pre-exists on main;
# that drift is deliberately excluded (surgical-change rule — mirrors 0046).

import core.models
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0046_disciplinarycase'),
        ('payroll', '0012_encrypt_employee_pii'),
    ]

    operations = [
        migrations.CreateModel(
            name='LeaveExcuseAutoResponse',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('work_date', models.DateField(db_index=True)),
                ('rule', models.CharField(help_text="Which auto-rule fired: 'A' (power cut) or 'B' (tracker / no IT ticket).", max_length=8)),
                ('sent_at', models.DateTimeField(blank=True, null=True, help_text='When the email actually went out. NULL = recorded but not sent (autosend OFF).')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leave_excuse_autoresponses', to='payroll.employee')),
            ],
            options={
                'verbose_name': 'Leave Excuse Auto Response',
                'verbose_name_plural': 'Leave Excuse Auto Responses',
                'ordering': ['-work_date'],
                'abstract': False,
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
        migrations.AlterUniqueTogether(
            name='leaveexcuseautoresponse',
            unique_together={('employee', 'work_date', 'rule')},
        ),
        migrations.AddIndex(
            model_name='leaveexcuseautoresponse',
            index=models.Index(fields=['work_date', 'rule'], name='hris_lexc_wd_rule_idx'),
        ),
    ]
