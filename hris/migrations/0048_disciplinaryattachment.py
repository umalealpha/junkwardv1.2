# Hand-trimmed to ONLY the new DisciplinaryAttachment table (CFO directive
# 2026-07-22 — evidence attachments on disciplinary cases). makemigrations also
# emits unrelated help_text/index drift on core/payroll/hris that pre-exists on
# main; that drift is deliberately excluded (surgical-change rule — mirrors 0046).

import core.models
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0047_leaveexcuseautoresponse'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DisciplinaryAttachment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(upload_to='disciplinary/%Y/%m/')),
                ('filename', models.CharField(blank=True, default='', help_text='Original upload filename.', max_length=255)),
                ('content_type', models.CharField(blank=True, default='', help_text='Browser-reported MIME type at upload.', max_length=120)),
                ('size', models.PositiveIntegerField(default=0, help_text='File size in bytes.')),
                ('case', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='hris.disciplinarycase')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='disciplinary_attachments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Disciplinary Attachment',
                'verbose_name_plural': 'Disciplinary Attachments',
                'ordering': ['-created_at'],
                'abstract': False,
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
    ]
