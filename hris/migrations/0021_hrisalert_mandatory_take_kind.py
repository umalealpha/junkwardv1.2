# Adds the 'leave_mandatory_take' choice to HRISAlert.kind (ELRA s.219 —
# mandatory 8 annual days). Choices are not DB-enforced, so this is a no-op at
# the database level; shipped to keep migration state in sync with the model.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hris", "0020_leavetype_elra_rules"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hrisalert",
            name="kind",
            field=models.CharField(
                max_length=40,
                choices=[
                    ("contract_expiry", "Contract 2 months before expiry"),
                    ("leave_balance_excess", "Leave balance exceeds legal cap"),
                    ("leave_mandatory_take", "Mandatory annual leave not taken (ELRA s.219)"),
                    ("review_due", "Performance review due"),
                    ("quarterly_review_due", "Quarterly review due"),
                    ("performance_concern", "Performance concern flagged"),
                ],
            ),
        ),
    ]
