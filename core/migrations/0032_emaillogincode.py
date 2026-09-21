from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_sod_senior_accountant_title'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailLoginCode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(db_index=True, max_length=254)),
                ('code_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('consumed', models.BooleanField(default=False)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='emaillogincode',
            index=models.Index(fields=['email', 'consumed'], name='core_emaill_email_5c9d2f_idx'),
        ),
    ]
