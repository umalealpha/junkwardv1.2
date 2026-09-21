import core.models
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0035_pip_directed_fields'),
        ('payroll', '0011_bank_details_import'),
    ]

    operations = [
        migrations.CreateModel(
            name='DevelopmentDialogue',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ref', models.CharField(db_index=True, max_length=40, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('department', models.CharField(blank=True, default='', max_length=200)),
                ('position', models.CharField(blank=True, default='', max_length=200)),
                ('period', models.CharField(blank=True, default='', max_length=120)),
                ('supervisor', models.CharField(blank=True, default='', max_length=200)),
                ('color', models.CharField(blank=True, default='', max_length=9)),
                ('performance', models.FloatField(default=0.5)),
                ('potential', models.FloatField(default=0.5)),
                ('overall', models.FloatField(blank=True, help_text='Overall Score (All Sections) as a percentage, 0-100.', null=True)),
                ('rating', models.CharField(blank=True, default='', max_length=120)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='development_dialogues', to='payroll.employee')),
            ],
            options={
                'verbose_name': 'Development Dialogue',
                'verbose_name_plural': 'Development Dialogues',
                'ordering': ['name'],
                'abstract': False,
            },
            bases=(core.models.AuditableMixin, models.Model),
        ),
    ]
