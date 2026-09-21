# Redundancy & re-hire transfer mode + leaver final leave pay (CFO 2026-09-05,
# Naomi Pheko ADIC → Unicoin). Hand-written: only the new columns, no drift.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hris", "0081_maternitydateoverride"),
    ]

    operations = [
        migrations.AddField(
            model_name="leaveencashment",
            name="kind",
            field=models.CharField(
                choices=[
                    ("encashment", "Leave encashment"),
                    ("settlement", "Leaver final leave pay"),
                ],
                db_index=True,
                default="encashment",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="leaveencashment",
            name="last_day",
            field=models.DateField(
                blank=True,
                help_text="Settlement only: the last working day the balance was accrued to.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="employeetransfer",
            name="mode",
            field=models.CharField(
                choices=[
                    ("carry", "Carry over (same record, leave travels with them)"),
                    (
                        "rehire",
                        "Redundancy & re-hire (terminate at source, new record at destination)",
                    ),
                ],
                db_index=True,
                default="carry",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="employeetransfer",
            name="leave_treatment",
            field=models.CharField(
                choices=[
                    ("payout", "Pay out the leave balance at the source entity"),
                    ("carry", "Carry the leave balance to the new record"),
                ],
                default="payout",
                help_text="Rehire mode only: what happens to the annual-leave balance at the source entity.",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="employeetransfer",
            name="new_email",
            field=models.EmailField(
                blank=True,
                default="",
                help_text="Rehire mode: the email address at the new entity (blank = keep the current one).",
                max_length=254,
            ),
        ),
        migrations.AddField(
            model_name="employeetransfer",
            name="new_employee",
            field=models.ForeignKey(
                blank=True,
                help_text="Rehire mode: the record created at the destination.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rehired_from_transfers",
                to="payroll.employee",
            ),
        ),
        migrations.AddField(
            model_name="employeetransfer",
            name="settlement",
            field=models.ForeignKey(
                blank=True,
                help_text="Rehire mode + payout: the leaver settlement raised.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rehire_transfers",
                to="hris.leaveencashment",
            ),
        ),
    ]
