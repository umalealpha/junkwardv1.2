import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("commissions", "0003_alter_commissionsubmissionline_commission_rate"),
    ]

    operations = [
        migrations.CreateModel(
            name="CommissionAmendment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("old_lines", models.JSONField(default=list)),
                ("new_lines", models.JSONField(default=list)),
                ("old_gross", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=18)),
                ("new_gross", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=18)),
                ("note", models.TextField(blank=True, default="")),
                ("actor", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+", to=settings.AUTH_USER_MODEL)),
                ("submission", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="amendments", to="commissions.commissionsubmission")),
            ],
            options={
                "ordering": ["-created_at"],
                "abstract": False,
            },
        ),
        migrations.AddIndex(
            model_name="commissionamendment",
            index=models.Index(fields=["submission", "created_at"],
                               name="commission_amend_sub_ct_idx"),
        ),
    ]
