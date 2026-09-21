"""Petty-cash dual sign-off (CFO 2026-07-16).

Two signatures now post a voucher: adds the first-signer stamp
(first_approved_by / first_approved_at) and the intermediate ONE_SIGNATURE
status. The second/final signature stays in approved_by/approved_at.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('petty_cash', '0005_pettycashvoucherreceipt'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='pettycashvoucher',
            name='first_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='pettycashvoucher',
            name='first_approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='petty_cash_vouchers_first_approved', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='pettycashvoucher',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('pending_approval', 'Pending Approval'), ('one_signature', 'One signature — needs 2nd'), ('posted', 'Posted'), ('reimbursed', 'Reimbursed'), ('rejected', 'Rejected')], default='draft', max_length=20),
        ),
    ]
