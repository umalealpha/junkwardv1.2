from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hris", "0012_employeetransfer_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="employeetransfer",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending_out", "Awaiting source (Transfer Out) approval"),
                    ("pending_in", "Out approved — awaiting destination (Transfer In)"),
                    ("scheduled", "Both approved — applies on the effective date"),
                    ("completed", "Both approved — applied to employee"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="pending_out",
                max_length=12,
            ),
        ),
    ]
