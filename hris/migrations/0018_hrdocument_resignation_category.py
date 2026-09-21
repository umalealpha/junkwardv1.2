"""Add a 'resignation' category to the HR document vault so leavers' resignation
letters can be filed alongside exit interviews (CFO 2026-06-25)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0017_hrisdailytaskrun'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hrdocument',
            name='category',
            field=models.CharField(
                choices=[
                    ('onboarding', 'Onboarding'),
                    ('policy', 'Policy'),
                    ('contract', 'Contract / agreement'),
                    ('resignation', 'Resignation'),
                    ('exit_interview', 'Exit interview'),
                    ('disciplinary', 'Disciplinary'),
                    ('other', 'Other'),
                ],
                db_index=True, default='onboarding', max_length=20),
        ),
    ]
