# Half-day leave support (Kago Tshutlhedi feature request 2026-07-13).
# Adds a day-type per boundary to LeaveRequest so a range can carry a half-day
# on the start and/or end. Additive with FULL defaults → zero behaviour change
# for every existing row and for any request that does not opt into half days.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hris", "0025_incentive_request"),
    ]

    operations = [
        migrations.AddField(
            model_name="leaverequest",
            name="start_day_type",
            field=models.CharField(
                max_length=4,
                default="full",
                choices=[
                    ("full", "Full day"),
                    ("am", "Half day (AM)"),
                    ("pm", "Half day (PM)"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="leaverequest",
            name="end_day_type",
            field=models.CharField(
                max_length=4,
                default="full",
                choices=[
                    ("full", "Full day"),
                    ("am", "Half day (AM)"),
                    ("pm", "Half day (PM)"),
                ],
            ),
        ),
    ]
