import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0078_incentiveline_payroll_amendment'),
    ]

    operations = [
        migrations.CreateModel(
            name='SuccessionNominee',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('rank', models.PositiveSmallIntegerField(default=1, help_text='1 = first choice, 2 = second, 3 = third.')),
                ('incumbent', models.ForeignKey(help_text='The role incumbent this person is named to succeed.', on_delete=django.db.models.deletion.CASCADE, related_name='succession_nominees_for', to='hris.hrisprofile')),
                ('nominee', models.ForeignKey(help_text='The named successor.', on_delete=django.db.models.deletion.CASCADE, related_name='succession_nominations', to='hris.hrisprofile')),
            ],
            options={
                'ordering': ['incumbent', 'rank'],
            },
        ),
        migrations.AddConstraint(
            model_name='successionnominee',
            constraint=models.UniqueConstraint(fields=('incumbent', 'nominee'), name='uniq_succession_nominee'),
        ),
    ]
