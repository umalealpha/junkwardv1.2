from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0041_dpochecklistrun'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(max_length=10, choices=[
                ('create', 'Create'), ('update', 'Update'), ('delete', 'Delete'),
                ('post', 'Post'), ('reverse', 'Reverse'), ('approve', 'Approve'),
                ('download', 'Download'), ('read', 'Read'),
            ]),
        ),
    ]
