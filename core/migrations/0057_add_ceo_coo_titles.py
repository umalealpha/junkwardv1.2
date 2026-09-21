from django.db import migrations, models


class Migration(migrations.Migration):
    """Add CEO and COO as access titles (functional executives).

    Choices-only change to UserProfile.title — no data migration, no DB
    schema change (CharField length unchanged at 30). CFO directive
    2026-08-04. Hand-written to touch ONLY this field and avoid pulling in
    unrelated model-state drift in core/payroll/hris.
    """

    dependencies = [
        ('core', '0056_speakerfeedback'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='title',
            field=models.CharField(
                choices=[
                    ('ceo', 'Chief Executive Officer'),
                    ('coo', 'Chief Operating Officer'),
                    ('cfo', 'Chief Financial Officer'),
                    ('finance_manager', 'Finance Manager'),
                    ('financial_controller', 'Financial Controller'),
                    ('claims_manager', 'Claims Manager'),
                    ('operations_manager', 'Operations Manager'),
                    ('hr_manager', 'HR Manager'),
                    ('claims_team_leader', 'Claims Team Leader'),
                    ('senior_claims_associate', 'Senior Claims Associate'),
                    ('junior_claims_associate', 'Junior Claims Associate'),
                    ('claims_intern', 'Claims Intern'),
                    ('accountant', 'Accountant'),
                    ('senior_accountant', 'Senior Accountant'),
                    ('bookkeeper', 'Bookkeeper'),
                    ('finance_analyst', 'Finance Analyst'),
                    ('auditor', 'Auditor (read-only)'),
                    ('executive', 'Executive (read-only)'),
                    ('operations', 'Operations Staff'),
                    ('system_api', 'System / API'),
                ],
                default='accountant',
                help_text='Job title — drives approval permissions.',
                max_length=30,
            ),
        ),
    ]
