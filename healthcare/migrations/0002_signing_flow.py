from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("healthcare", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendoronboarding",
            name="signing_status",
            field=models.CharField(
                default="draft", max_length=20,
                choices=[
                    ("draft", "Draft (staff filling)"),
                    ("ready", "Ready for signature"),
                    ("sent", "Signature link sent"),
                    ("viewed", "Link opened by signer"),
                    ("signed", "Signed"),
                    ("completed", "Completed (agreement emailed)"),
                    ("expired", "Link expired"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="vendoronboarding",
            name="prepared_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="vendor_onboardings_prepared", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="vendoronboarding",
            name="prepared_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="VendorSignatureRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("sent_to_email", models.EmailField(blank=True, max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("viewed_at", models.DateTimeField(blank=True, null=True)),
                ("signed_at", models.DateTimeField(blank=True, null=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("signer_ip", models.CharField(blank=True, max_length=45)),
                ("signer_user_agent", models.TextField(blank=True)),
                ("consent_accepted_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="vendor_sig_requests", to=settings.AUTH_USER_MODEL)),
                ("onboarding", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="signature_requests", to="healthcare.vendoronboarding")),
            ],
            options={"verbose_name": "Vendor signature request", "ordering": ["-created_at"]},
        ),
    ]
