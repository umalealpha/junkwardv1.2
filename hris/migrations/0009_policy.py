"""Add Policy model — ARIA-indexable policy register."""
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0008_leavetype_rules'),
    ]

    operations = [
        migrations.CreateModel(
            name='Policy',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code',           models.CharField(max_length=40, unique=True)),
                ('title',          models.CharField(max_length=200)),
                ('version',        models.CharField(default='v2023', max_length=20)),
                ('owner',          models.CharField(default='HR', max_length=80)),
                ('approval_date',  models.DateField(blank=True, null=True)),
                ('review_due',     models.DateField(blank=True, null=True)),
                ('body_md',        models.TextField(blank=True, default='')),
                ('source_doc',     models.CharField(blank=True, default='', max_length=200)),
                ('is_active',      models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['code'],
                'verbose_name': 'Policy',
                'verbose_name_plural': 'Policies',
            },
        ),
    ]
