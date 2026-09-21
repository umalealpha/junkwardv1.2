import core.models
import django.db.models.deletion
import uuid
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0040_alter_hrdocument_category_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PerformanceTarget',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('metric', models.CharField(help_text='e.g. "New sales", "Claims closed"', max_length=200)),
                ('target_value', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('unit', models.CharField(choices=[('BWP', 'Pula (BWP)'), ('count', 'Count / number'), ('percent', 'Percentage')], default='BWP', max_length=10)),
                ('cadence', models.CharField(choices=[('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('annual', 'Annual')], default='monthly', max_length=10)),
                ('source', models.CharField(choices=[('manual', 'Manager enters the actual'), ('health_quotes', 'Auto: group-health new sales (omni)')], default='manual', max_length=20)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('note', models.TextField(blank=True, default='', help_text='From the job description — context for the target.')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='performance_targets_created', to=settings.AUTH_USER_MODEL)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='performance_targets', to='hris.hrisprofile')),
            ],
            options={
                'verbose_name': 'Performance Target',
                'verbose_name_plural': 'Performance Targets',
                'ordering': ['profile__employee__full_name', 'metric'],
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
        migrations.CreateModel(
            name='PerformanceTargetResult',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('period_month', models.PositiveSmallIntegerField()),
                ('period_year', models.PositiveSmallIntegerField()),
                ('target_value', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=14)),
                ('actual_value', models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ('achieved', models.BooleanField(blank=True, null=True)),
                ('source_used', models.CharField(blank=True, default='', max_length=20)),
                ('note', models.TextField(blank=True, default='')),
                ('checkin', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='target_results', to='hris.monthlycheckin')),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='performance_target_results', to='hris.hrisprofile')),
                ('recorded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='performance_results_recorded', to=settings.AUTH_USER_MODEL)),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results', to='hris.performancetarget')),
            ],
            options={
                'verbose_name': 'Performance Target Result',
                'verbose_name_plural': 'Performance Target Results',
                'ordering': ['-period_year', '-period_month'],
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
        migrations.AddIndex(
            model_name='performancetarget',
            index=models.Index(fields=['profile', 'active'], name='perftarget_profile_active_idx'),
        ),
        migrations.AddIndex(
            model_name='performancetargetresult',
            index=models.Index(fields=['profile', 'period_year', 'period_month'], name='perfresult_profile_period_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='performancetargetresult',
            unique_together={('target', 'period_year', 'period_month')},
        ),
    ]
