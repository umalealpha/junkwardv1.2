"""HR document vault (Dorothy 2026-06-26) — onboarding/policy/exit/disciplinary
files stored in media/hr_documents/."""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('hris', '0015_monthlycheckin_pip'),
    ]

    operations = [
        migrations.CreateModel(
            name='HRDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=200)),
                ('category', models.CharField(choices=[('onboarding', 'Onboarding'), ('policy', 'Policy'), ('contract', 'Contract / agreement'), ('exit_interview', 'Exit interview'), ('disciplinary', 'Disciplinary'), ('other', 'Other')], db_index=True, default='onboarding', max_length=20)),
                ('file', models.FileField(upload_to='hr_documents/')),
                ('description', models.TextField(blank=True, default='')),
                ('is_personal', models.BooleanField(default=False)),
                ('employee_name', models.CharField(blank=True, default='', max_length=200)),
                ('employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hr_documents', to='hris.hrisprofile')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hr_documents_uploaded', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'HR document',
                'verbose_name_plural': 'HR documents',
                'ordering': ['category', 'title'],
            },
        ),
    ]
