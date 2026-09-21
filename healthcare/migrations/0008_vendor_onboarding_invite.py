from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("healthcare", "0007_health_quote_discount"),
    ]

    operations = [
        migrations.CreateModel(
            name="VendorOnboardingInvite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("invited_email", models.EmailField(blank=True, max_length=254)),
                ("invited_name", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("viewed_at", models.DateTimeField(blank=True, null=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("submitter_ip", models.CharField(blank=True, max_length=45)),
                ("submitter_user_agent", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="vendor_onboarding_invites", to=settings.AUTH_USER_MODEL)),
                ("onboarding", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="invites", to="healthcare.vendoronboarding")),
            ],
            options={"verbose_name": "Vendor onboarding invite", "ordering": ["-created_at"]},
        ),
    ]
