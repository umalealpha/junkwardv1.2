from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0047_dpoworkbook'),
    ]

    operations = [
        migrations.CreateModel(
            name='ComplianceBrainSummary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('as_of', models.DateField()),
                ('source_url', models.CharField(blank=True, default='', max_length=300)),
                ('fetched_ok', models.BooleanField(default=False)),
                ('counts', models.JSONField(default=dict)),
                ('kyc', models.JSONField(default=dict)),
                ('ai_narrative', models.TextField(blank=True, default='')),
                ('ai_engine', models.CharField(blank=True, default='', max_length=40)),
                ('note', models.CharField(blank=True, default='', max_length=300)),
            ],
            options={
                'verbose_name': 'Compliance Brain Summary',
                'verbose_name_plural': 'Compliance Brain Summaries',
                'ordering': ['-created_at'],
            },
        ),
    ]
