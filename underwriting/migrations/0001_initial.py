import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0002_company'),   # company FK target lives in 0002, not 0001
    ]

    operations = [
        migrations.CreateModel(
            name='UnderwritingDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('doctype', models.CharField(choices=[('cn', 'Cover Note'), ('cnfi', 'Cover Note — financed'), ('wca', 'WCA Certificate')], max_length=8)),
                ('fmt', models.CharField(blank=True, choices=[('orig', 'Original'), ('cool', 'Cool'), ('royal', 'Royal'), ('formal', 'Formal')], default='', max_length=10)),
                ('fields', models.JSONField(blank=True, default=dict)),
                ('policy_number', models.CharField(blank=True, default='', max_length=60)),
                ('insured_name', models.CharField(blank=True, default='', max_length=200)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('issued', 'Issued')], db_index=True, default='draft', max_length=10)),
                ('pdf_bytes', models.BinaryField(blank=True, editable=False, null=True)),
                ('pdf_size', models.PositiveIntegerField(default=0)),
                ('issued_at', models.DateTimeField(blank=True, null=True)),
                ('emailed_to', models.CharField(blank=True, default='', max_length=254)),
                ('emailed_at', models.DateTimeField(blank=True, null=True)),
                ('company', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='underwriting_documents', to='core.company')),
                ('issued_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='underwriting_documents_issued', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Underwriting document',
                'verbose_name_plural': 'Underwriting documents',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='underwritingdocument',
            index=models.Index(fields=['doctype', 'status'], name='uw_doc_dt_status_idx'),
        ),
        migrations.AddIndex(
            model_name='underwritingdocument',
            index=models.Index(fields=['company', '-created_at'], name='uw_doc_company_created_idx'),
        ),
    ]
