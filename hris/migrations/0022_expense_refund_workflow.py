# Generated 2026-07-13 — expense-refund workflow (CFO 2026-07-13).
# Hand-trimmed to ONLY the ExpenseClaim changes; unrelated monthlycheckin /
# performanceimprovementplan help_text drift excluded.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hris", "0021_hrisalert_mandatory_take_kind"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="expenseclaim", name="approver",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="expense_claims_to_process", to=settings.AUTH_USER_MODEL,
                help_text="The senior accountant chosen to process this refund."),
        ),
        migrations.AddField(
            model_name="expenseclaim", name="payment_proof",
            field=models.FileField(
                blank=True, null=True, upload_to="expense_claims/proof/",
                help_text="FNB payment proof uploaded by the accountant."),
        ),
        migrations.AddField(
            model_name="expenseclaim", name="processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="expenseclaim", name="processed_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="expense_claims_processed", to=settings.AUTH_USER_MODEL,
                help_text="Senior accountant who loaded the payment + uploaded proof."),
        ),
        migrations.AddField(
            model_name="expenseclaim", name="reject_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="expenseclaim", name="submitted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="expenseclaim", name="status",
            field=models.CharField(
                max_length=12, default="draft",
                choices=[
                    ("draft", "Draft"), ("submitted", "With accountant"),
                    ("pending_cfo", "With CFO"), ("approved", "Approved"),
                    ("rejected", "Returned to requester"), ("paid", "Paid"),
                ]),
        ),
        migrations.CreateModel(
            name="ExpenseClaimAttachment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("file", models.FileField(upload_to="expense_claims/invoices/")),
                ("claim", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="invoices", to="hris.expenseclaim")),
                ("uploaded_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Expense Claim Invoice",
                     "verbose_name_plural": "Expense Claim Invoices",
                     "ordering": ["created_at"], "abstract": False},
        ),
    ]
