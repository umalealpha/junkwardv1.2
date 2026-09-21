import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fnb', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FnbCredential',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('client_id', models.CharField(blank=True, default='', max_length=128)),
                ('client_secret_enc', models.TextField(blank=True, default='', help_text='Fernet-encrypted; never exposed.')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fnb_credentials_updated', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'FNB Credential',
                'verbose_name_plural': 'FNB Credentials',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
    ]
