from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0039_perf_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='AISpeedLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('feature', models.CharField(blank=True, default='', max_length=64)),
                ('engine', models.CharField(max_length=32)),
                ('model_name', models.CharField(blank=True, default='', max_length=80)),
                ('ms', models.PositiveIntegerField()),
                ('ok', models.BooleanField(default=True)),
                ('escalated', models.BooleanField(default=False)),
            ],
            options={
                'verbose_name': 'AI speed log',
                'ordering': ['-created_at'],
            },
        ),
    ]
