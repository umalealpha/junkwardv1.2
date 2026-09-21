import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0069_brief_note'),
    ]

    operations = [
        migrations.CreateModel(
            name='AriaPopup',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('message', models.TextField()),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aria_popups_sent', to=settings.AUTH_USER_MODEL)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aria_popups_received', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Aria Popup',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['recipient', 'is_read', 'created_at'], name='core_ariapo_recipie_eaa4ab_idx'),
                ],
            },
        ),
    ]
