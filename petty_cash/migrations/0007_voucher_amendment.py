"""
Record what the requester originally asked for when a custodian amends a voucher.

Keetile asked to be able to correct the amount and the GL line instead of returning the voucher
for a typo; the CFO approved it on 5 August 2026. Allowing it is fine — losing the original figure
would not be. These columns keep the requester's number, who changed it, when, and why, so the
float still reconciles months later.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('petty_cash', '0006_pettycashvoucher_dual_signoff'),
        ('ledger', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='pettycashvoucher',
            name='original_amount',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True,
                help_text='What the requester asked for, kept when a custodian amends the amount.'),
        ),
        migrations.AddField(
            model_name='pettycashvoucher',
            name='original_expense_account',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='ledger.account',
                help_text='The GL account the requester chose, kept when a custodian changes it.'),
        ),
        migrations.AddField(
            model_name='pettycashvoucher',
            name='amended_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='petty_cash_amendments', to='auth.user'),
        ),
        migrations.AddField(
            model_name='pettycashvoucher',
            name='amended_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pettycashvoucher',
            name='amend_reason',
            field=models.TextField(
                blank=True, default='',
                help_text='Why the custodian changed the amount or the GL line. Required to amend.'),
        ),
    ]
