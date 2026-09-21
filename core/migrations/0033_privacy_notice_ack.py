# Staff privacy-notice acknowledgement (CFO directive 2026-07-09).
# NOTE: only the PrivacyNoticeAcknowledgement model is created here. Unrelated
# index/field drift that makemigrations also detected is intentionally excluded
# — that is a separate pre-existing drift item, not part of this feature.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_emaillogincode"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PrivacyNoticeAcknowledgement",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.CharField(max_length=32)),
                (
                    "signed_name",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="privacy_acknowledgements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "abstract": False,
            },
        ),
        migrations.AddIndex(
            model_name="privacynoticeacknowledgement",
            index=models.Index(fields=["version"], name="core_privack_ver_idx"),
        ),
        migrations.AddConstraint(
            model_name="privacynoticeacknowledgement",
            constraint=models.UniqueConstraint(
                fields=("user", "version"), name="uniq_privacy_ack_user_version"
            ),
        ),
    ]
