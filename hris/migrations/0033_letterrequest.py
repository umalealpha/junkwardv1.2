import core.models
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0032_leaverequest_requested_approver'),
        ('payroll', '0011_bank_details_import'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LetterRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('letter_type', models.CharField(choices=[('employment_confirmation', 'Employment confirmation')], default='employment_confirmation', max_length=40)),
                ('addressee', models.CharField(default='To Whom It May Concern', max_length=200)),
                ('purpose', models.CharField(blank=True, default='', max_length=200)),
                ('status', models.CharField(choices=[('pending', 'Awaiting manager sign-off'), ('issued', 'Signed off — letter issued'), ('declined', 'Declined')], db_index=True, default='pending', max_length=10)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('decline_reason', models.CharField(blank=True, default='', max_length=300)),
                ('reference', models.CharField(blank=True, db_index=True, default='', max_length=40)),
                ('issued_snapshot', models.JSONField(blank=True, null=True)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='letters_decided', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='letter_requests', to='payroll.employee')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='letters_requested', to=settings.AUTH_USER_MODEL)),
                ('signatory', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='letters_signed', to='payroll.employee')),
            ],
            options={
                'verbose_name': 'Letter Request',
                'ordering': ['-created_at'],
                'abstract': False,
                'indexes': [models.Index(fields=['status', '-created_at'], name='hris_letter_status_f6660e_idx'), models.Index(fields=['employee', '-created_at'], name='hris_letter_employe_336d37_idx')],
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
    ]
