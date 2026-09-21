from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0040_aispeedlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='DpoChecklistRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('period', models.CharField(db_index=True, max_length=7, unique=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('submitted', 'Submitted'), ('overdue', 'Overdue')], default='open', max_length=10)),
                ('due_date', models.DateField()),
                ('responses', models.JSONField(blank=True, default=dict)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('disciplinary_task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.omnitask')),
                ('submitted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'DPO checklist run',
                'ordering': ['-period'],
            },
        ),
    ]
