import uuid
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0006_expense_claim'),
        ('payroll', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='HRISAlert',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('kind', models.CharField(max_length=40, choices=[
                    ('contract_expiry',     'Contract 2 months before expiry'),
                    ('leave_balance_excess','Leave balance exceeds legal cap'),
                    ('review_due',          'Performance review due'),
                    ('quarterly_review_due','Quarterly review due'),
                    ('performance_concern', 'Performance concern flagged'),
                ])),
                ('severity', models.CharField(default='medium', max_length=10, choices=[
                    ('low','Low'),('medium','Medium'),('high','High'),('critical','Critical'),
                ])),
                ('state', models.CharField(default='open', max_length=15, choices=[
                    ('open','Open'),('acknowledged','Acknowledged'),
                    ('dismissed','Dismissed'),('resolved','Resolved'),
                ])),
                ('target_kind', models.CharField(blank=True, default='', max_length=40,
                    help_text='e.g. employment_contract / leave_request / review')),
                ('target_id', models.CharField(blank=True, default='', max_length=40)),
                ('title', models.CharField(max_length=200)),
                ('detail', models.TextField(blank=True, default='')),
                ('due_date', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('acknowledged_by', models.CharField(blank=True, default='', max_length=80)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('employee', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='hris_alerts', to='payroll.employee')),
                ('profile', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='alerts', to='hris.hrisprofile')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='HRISAlert',
            index=models.Index(fields=['state', 'kind'], name='hris_alert_state_kind_idx'),
        ),
        migrations.AddIndex(
            model_name='HRISAlert',
            index=models.Index(fields=['employee', 'state'], name='hris_alert_emp_state_idx'),
        ),
        migrations.AddIndex(
            model_name='HRISAlert',
            index=models.Index(fields=['kind', 'target_id'], name='hris_alert_kind_target_idx'),
        ),
    ]
