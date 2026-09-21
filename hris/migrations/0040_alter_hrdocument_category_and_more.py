# Add the 'development_dialogue' category to HRDocument (CFO 2026-07-18).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hris", "0039_developmentdialogue_locked"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hrdocument",
            name="category",
            field=models.CharField(
                choices=[
                    ("onboarding", "Onboarding"),
                    ("preboarding", "Pre-boarding pack"),
                    ("cv", "CV / résumé"),
                    ("certificate", "Certificate / qualification"),
                    ("policy", "Policy"),
                    ("contract", "Contract / agreement"),
                    ("resignation", "Resignation"),
                    ("exit_interview", "Exit interview"),
                    ("disciplinary", "Disciplinary"),
                    ("development_dialogue", "Development Dialogue"),
                    ("other", "Other"),
                ],
                db_index=True,
                default="onboarding",
                max_length=20,
            ),
        ),
    ]
