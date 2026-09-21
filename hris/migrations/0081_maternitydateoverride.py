import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import hris.maternity_override_models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hris', '0080_onboardingrequest_annual_leave_entitlement'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaternityDateOverride',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('old_end_date', models.DateField()),
                ('new_end_date', models.DateField()),
                ('reason_code', models.CharField(choices=[('complications_extension', 'Complications extension'), ('stillbirth_miscarriage', 'Stillbirth or miscarriage'), ('data_entry_error', 'Data entry error')], max_length=32)),
                ('certificate', models.FileField(upload_to=hris.maternity_override_models._override_cert_path)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('pending', 'Pending second approval'), ('approved', 'Approved and applied'), ('rejected', 'Rejected')], db_index=True, default='pending', max_length=10)),
                ('notes', models.TextField(blank=True, default='')),
                ('leave_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='date_overrides', to='hris.leaverequest')),
                ('proposed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='maternity_overrides_proposed', to=settings.AUTH_USER_MODEL)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='maternity_overrides_approved', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Maternity Date Override',
                'verbose_name_plural': 'Maternity Date Overrides',
                'ordering': ['-created_at'],
            },
        ),
    ]
