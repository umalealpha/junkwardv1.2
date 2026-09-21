# Customer activity logging (fitness / healthy eating / steps) — 2026-06-26.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0006_nexusemailquota'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerActivity',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('fitness', 'Fitness Activity'), ('healthy_eating', 'Healthy Eating'), ('steps', 'Daily Steps')], max_length=20)),
                ('detail', models.CharField(blank=True, default='', max_length=200)),
                ('value', models.IntegerField(default=0, help_text='e.g. step count')),
                ('points_awarded', models.IntegerField(default=0)),
                ('occurred_at', models.DateTimeField()),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activities', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Activity', 'ordering': ['-occurred_at'], 'abstract': False},
        ),
    ]
