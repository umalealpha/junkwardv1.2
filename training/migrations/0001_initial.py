import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TrainingModule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('slug', models.SlugField(max_length=64, unique=True)),
                ('title', models.CharField(max_length=200)),
                ('subtitle', models.CharField(blank=True, default='', max_length=300)),
                ('body_html', models.TextField(blank=True, default='',
                                               help_text='Main lesson body. HTML allowed.')),
                ('video_url', models.URLField(blank=True, default='')),
                ('pdf', models.FileField(blank=True, null=True,
                                         upload_to='training/%Y/',
                                         help_text='Optional slide deck / handbook.')),
                ('pass_mark_pct', models.PositiveIntegerField(default=80)),
                ('open_from', models.DateField()),
                ('open_until', models.DateField()),
                ('mandatory_for_all', models.BooleanField(default=True)),
                ('is_published', models.BooleanField(db_index=True, default=False)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='training_modules_created',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-open_from', 'title']},
        ),
        migrations.CreateModel(
            name='TrainingQuestion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('prompt', models.TextField()),
                ('option_a', models.CharField(max_length=500)),
                ('option_b', models.CharField(max_length=500)),
                ('option_c', models.CharField(blank=True, default='', max_length=500)),
                ('option_d', models.CharField(blank=True, default='', max_length=500)),
                ('correct_letter', models.CharField(
                    choices=[('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')],
                    max_length=1)),
                ('module', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='questions', to='training.trainingmodule')),
            ],
            options={'ordering': ['order', 'created_at']},
        ),
        migrations.CreateModel(
            name='TrainingAttempt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('answers', models.JSONField(
                    blank=True, default=dict,
                    help_text="{question_id: 'A'/'B'/'C'/'D'}")),
                ('score_pct', models.PositiveIntegerField(default=0)),
                ('passed', models.BooleanField(db_index=True, default=False)),
                ('completed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('certificate_number', models.CharField(
                    blank=True, db_index=True, default='', max_length=32)),
                ('module', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='attempts', to='training.trainingmodule')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='training_attempts',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-completed_at'],
                'indexes': [
                    models.Index(fields=['module', 'user'],
                                 name='training_tr_module__5887ef_idx'),
                    models.Index(fields=['module', 'passed'],
                                 name='training_tr_module__2edeeb_idx'),
                ],
            },
        ),
    ]
