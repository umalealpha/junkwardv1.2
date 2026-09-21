from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0036_developmentdialogue'),
    ]

    operations = [
        migrations.AddField(
            model_name='developmentdialogue',
            name='email',
            field=models.CharField(blank=True, db_index=True, default='', max_length=200),
        ),
    ]
