# Generated 2026-07-13 — ExpenseClaim.gl_account link (CFO: accountant links the GL).
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hris", "0022_expense_refund_workflow"),
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="expenseclaim",
            name="gl_account",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="expense_claims", to="ledger.account",
                help_text="Expense GL account to post this refund to."),
        ),
    ]
