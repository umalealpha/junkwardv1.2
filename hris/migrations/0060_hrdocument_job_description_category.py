"""Add the 'Job Description' category to the HR Document Vault.

Hand-written (not makemigrations) to touch ONLY HRDocument.category and avoid
pulling in the pre-existing help_text/index drift in the hris/core/payroll apps.
Choices are a Python-level constraint, so this only updates field metadata.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0059_arjun_dont_track'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hrdocument',
            name='category',
            field=models.CharField(
                choices=[
                    ('onboarding', 'Onboarding'),
                    ('preboarding', 'Pre-boarding pack'),
                    ('cv', 'CV / résumé'),
                    ('certificate', 'Certificate / qualification'),
                    ('policy', 'Policy'),
                    ('contract', 'Contract / agreement'),
                    ('job_description', 'Job Description'),
                    ('resignation', 'Resignation'),
                    ('exit_interview', 'Exit interview'),
                    ('disciplinary', 'Disciplinary'),
                    ('development_dialogue', 'Development Dialogue'),
                    ('other', 'Other'),
                ],
                db_index=True,
                default='onboarding',
                max_length=20,
            ),
        ),
    ]
