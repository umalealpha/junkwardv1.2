from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0053_alter_processordpa_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='MailAutoReply',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('sender_email', models.EmailField(db_index=True, max_length=254, unique=True)),
                ('last_sent_at', models.DateTimeField()),
                ('times_sent', models.PositiveIntegerField(default=1)),
                ('last_subject', models.CharField(blank=True, default='', max_length=300)),
                ('last_message_id', models.CharField(blank=True, default='', max_length=300)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Mail auto-reply',
                'verbose_name_plural': 'Mail auto-replies',
                'ordering': ['-last_sent_at'],
            },
        ),
    ]
