# Generated for the Alpha Staff Rewards module (staff_rewards) — hand-written.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0009_payslip_source_currency'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffPointsAccount',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('points_balance', models.IntegerField(default=0, help_text='Overall staff points (sum of all pillars).')),
                ('innovation_points', models.IntegerField(default=0)),
                ('business_impact_points', models.IntegerField(default=0)),
                ('health_wellness_points', models.IntegerField(default=0)),
                ('employee', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='staff_points_account', to='payroll.employee')),
            ],
            options={
                'verbose_name': 'Staff Points Account',
                'verbose_name_plural': 'Staff Points Accounts',
                'ordering': ['-points_balance'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='StaffSubmission',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('pillar', models.CharField(choices=[('innovation', 'Innovation'), ('business_impact', 'Business Impact'), ('health_wellness', 'Health & Wellness')], max_length=20)),
                ('feature_code', models.CharField(choices=[('profdev', 'Professional Development & Upskilling'), ('bizdev', 'Business Development & Partnerships'), ('fitness', 'Running Clubs & Fitness Activities'), ('compliment', 'Customer Compliments & Positive Feedback'), ('meal', 'Healthy Eating & Nutrition'), ('steps', 'Daily Step Goals')], max_length=20)),
                ('payload', models.JSONField(blank=True, default=dict, help_text='Activity inputs/references ONLY — Member ID, case ref, course ref. No personal names, no raw images, no location data (DPA).')),
                ('status', models.CharField(choices=[('pending', 'Pending approval'), ('approved', 'Approved'), ('rejected', 'Rejected')], db_index=True, default='pending', max_length=10)),
                ('points_awarded', models.IntegerField(default=0, help_text='Computed on approval from points_rules.')),
                ('maker_email', models.EmailField(blank=True, default='', help_text='Who submitted (snapshot at submit time).', max_length=254)),
                ('approver_email', models.EmailField(blank=True, default='', max_length=254)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('reason', models.TextField(blank=True, default='', help_text='Decision note (mandatory context on reject).')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='staff_submissions', to='payroll.employee')),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='staff_submissions_decided', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Staff Submission',
                'verbose_name_plural': 'Staff Submissions',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='StaffPointsTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('points', models.IntegerField(help_text='+ on earn / adjust.')),
                ('kind', models.CharField(choices=[('earn', 'Earn'), ('adjust', 'Adjustment')], default='earn', max_length=10)),
                ('detail', models.CharField(blank=True, default='', max_length=300)),
                ('occurred_at', models.DateTimeField()),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transactions', to='staff_rewards.staffpointsaccount')),
                ('submission', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transactions', to='staff_rewards.staffsubmission')),
            ],
            options={
                'verbose_name': 'Staff Points Transaction',
                'verbose_name_plural': 'Staff Points Transactions',
                'ordering': ['-occurred_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='staffsubmission',
            index=models.Index(fields=['status', '-created_at'], name='staff_rewar_status_3f8c2a_idx'),
        ),
        migrations.AddIndex(
            model_name='staffsubmission',
            index=models.Index(fields=['employee', 'feature_code'], name='staff_rewar_employe_7b1d4e_idx'),
        ),
    ]
