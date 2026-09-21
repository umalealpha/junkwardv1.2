from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0037_omnitask_performance_points'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='job_title',
            field=models.CharField(
                blank=True, default='', max_length=150,
                help_text='HR job title (from Odoo) — display only, does NOT affect permissions.',
            ),
        ),
    ]
