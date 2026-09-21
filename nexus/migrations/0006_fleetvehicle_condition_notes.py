from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nexus", "0005_fleetvehicle_colour_fleetvehicle_home_yard_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="fleetvehicle",
            name="condition_notes",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
    ]
