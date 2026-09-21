# In-app customer feedback / short survey — 2026-07-23.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0007_customeractivity'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerFeedback',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('rating', models.PositiveSmallIntegerField(default=0, help_text='1-5 stars')),
                ('area', models.CharField(choices=[('overall', 'Overall app'), ('drive', 'Nexus Drive'), ('wellness', 'Wellness'), ('rewards', 'Rewards'), ('other', 'Something else')], default='overall', max_length=20)),
                ('comment', models.TextField(blank=True, default='')),
                ('wants', models.TextField(blank=True, default='', help_text='what the member wants next')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Feedback', 'ordering': ['-created_at'], 'abstract': False},
        ),
    ]
