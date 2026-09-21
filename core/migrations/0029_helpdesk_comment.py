from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("core", "0028_frozen_governance"),
    ]
    operations = [
        migrations.CreateModel(
            name="HelpdeskComment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ticket_id", models.CharField(db_index=True, max_length=20)),
                ("author", models.CharField(default="Prathap Ganesharajah", max_length=120)),
                ("text", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("source", models.CharField(default="claude_code", max_length=30)),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
