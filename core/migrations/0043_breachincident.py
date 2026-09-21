from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0042_auditlog_read_action'),
    ]

    operations = [
        migrations.CreateModel(
            name='BreachIncident',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True, default='')),
                ('discovered_at', models.DateTimeField()),
                ('severity', models.CharField(choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')], default='medium', max_length=10)),
                ('reportable', models.BooleanField(default=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('contained', 'Contained'), ('closed', 'Closed')], default='open', max_length=10)),
                ('idpc_notified', models.BooleanField(default=False)),
                ('idpc_notified_at', models.DateTimeField(blank=True, null=True)),
                ('subjects_notified', models.BooleanField(default=False)),
                ('remedial', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Breach incident',
                'ordering': ['-discovered_at'],
            },
        ),
    ]
