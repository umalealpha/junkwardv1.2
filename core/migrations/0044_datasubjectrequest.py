from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0043_breachincident'),
    ]

    operations = [
        migrations.CreateModel(
            name='DataSubjectRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_name', models.CharField(max_length=200)),
                ('subject_email', models.CharField(blank=True, default='', max_length=254)),
                ('subject_type', models.CharField(default='customer', max_length=20)),
                ('kind', models.CharField(choices=[('access', 'Access'), ('rectify', 'Rectification'), ('erase', 'Erasure'), ('restrict', 'Restriction'), ('object', 'Objection'), ('portability', 'Portability')], default='access', max_length=15)),
                ('details', models.TextField(blank=True, default='')),
                ('status', models.CharField(choices=[('open', 'Open'), ('in_progress', 'In progress'), ('completed', 'Completed'), ('rejected', 'Rejected')], default='open', max_length=12)),
                ('received_at', models.DateTimeField()),
                ('due_date', models.DateField()),
                ('resolution', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('handled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Data-subject request',
                'ordering': ['-received_at'],
            },
        ),
    ]
