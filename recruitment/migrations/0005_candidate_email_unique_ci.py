"""One identity per email (Manus QC 2026-08-27).

Case-insensitive unique on Candidate.email, blank exempt. Prod recruitment
tables are empty at apply time, so this cannot fail on existing duplicates.
"""
from django.db import migrations, models
import django.db.models.functions.text


class Migration(migrations.Migration):

    dependencies = [
        ('recruitment', '0004_authoritytorecruit_application_and_more'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='candidate',
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower('email'),
                condition=models.Q(('email', ''), _negated=True),
                name='uniq_candidate_email_ci',
            ),
        ),
    ]
