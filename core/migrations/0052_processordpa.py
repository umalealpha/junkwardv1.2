from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0051_sync_model_state_2026_07_26'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProcessorDPA',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('processor_name', models.CharField(max_length=200)),
                ('processor_email', models.EmailField(max_length=254)),
                ('country', models.CharField(blank=True, default='', max_length=100)),
                ('adequate', models.BooleanField(default=False)),
                ('purpose', models.CharField(default='', max_length=400)),
                ('data_categories', models.CharField(blank=True, default='', max_length=400)),
                ('reference', models.CharField(blank=True, default='', max_length=40)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('sent', 'Sent for signature'), ('signed', 'Signed')], default='draft', max_length=12)),
                ('document_html', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('signed_at', models.DateTimeField(blank=True, null=True)),
                ('signatory_name', models.CharField(blank=True, default='', max_length=200)),
                ('signatory_title', models.CharField(blank=True, default='', max_length=200)),
                ('signatory_ip', models.CharField(blank=True, default='', max_length=64)),
                ('signed_doc_sha256', models.CharField(blank=True, default='', max_length=64)),
            ],
            options={
                'verbose_name': 'Processor DPA',
                'verbose_name_plural': 'Processor DPAs',
                'ordering': ['-created_at'],
            },
        ),
    ]
