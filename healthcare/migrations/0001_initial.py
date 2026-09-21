from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VendorOnboarding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference_number", models.CharField(db_index=True, max_length=32, unique=True)),
                ("company_name", models.CharField(max_length=255)),
                ("registration_number", models.CharField(max_length=64)),
                ("registration_date", models.CharField(blank=True, max_length=64)),
                ("registered_address", models.TextField(blank=True)),
                ("tin", models.CharField(blank=True, max_length=32)),
                ("vat_number", models.CharField(blank=True, max_length=32)),
                ("directors", models.JSONField(blank=True, default=list)),
                ("practitioner_name", models.CharField(blank=True, max_length=255)),
                ("council_type", models.CharField(choices=[("BHPC", "Botswana Health Professions Council"), ("PharmacyCouncil", "Botswana Pharmacy Council"), ("Other", "Other")], default="BHPC", max_length=32)),
                ("council_registration_number", models.CharField(blank=True, max_length=64)),
                ("discipline", models.CharField(blank=True, max_length=128)),
                ("practice_address", models.TextField(blank=True)),
                ("bank_name", models.CharField(blank=True, max_length=128)),
                ("branch_name", models.CharField(blank=True, max_length=128)),
                ("branch_code", models.CharField(blank=True, max_length=32)),
                ("account_holder", models.CharField(blank=True, max_length=255)),
                ("account_number", models.CharField(blank=True, max_length=32)),
                ("service_category", models.CharField(blank=True, max_length=128)),
                ("services_offered", models.TextField(blank=True)),
                ("trading_name", models.CharField(blank=True, max_length=255)),
                ("representative_name", models.CharField(blank=True, max_length=255)),
                ("representative_capacity", models.CharField(blank=True, max_length=128)),
                ("principal_place_of_business", models.TextField(blank=True)),
                ("effective_date", models.CharField(blank=True, max_length=64)),
                ("contact_tel", models.CharField(blank=True, max_length=64)),
                ("contact_email", models.EmailField(blank=True, max_length=254)),
                ("agreement_accepted", models.BooleanField(default=False)),
                ("agreement_accepted_at", models.DateTimeField(blank=True, null=True)),
                ("consent_version", models.CharField(blank=True, max_length=64)),
                ("consent_at", models.DateTimeField(blank=True, null=True)),
                ("signatory_full_name", models.CharField(blank=True, max_length=255)),
                ("signed_at", models.DateTimeField(blank=True, null=True)),
                ("signature_data_url", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("pending_review", "Pending review"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending_review", max_length=20)),
                ("deepseek_used", models.BooleanField(default=False)),
                ("agreement_email_sent", models.BooleanField(default=False)),
                ("agreement_emailed_to", models.CharField(blank=True, max_length=255)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("submitted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="vendor_onboardings", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Vendor onboarding",
                "verbose_name_plural": "Vendor onboardings",
                "ordering": ["-created_at"],
            },
        ),
    ]
