"""Make PettyCashVoucher.expense_account optional.

CFO directive 2026-08-10: the person who RAISES a petty-cash voucher (e.g. an
Executive Assistant requesting cash for a staff-welfare cake) no longer codes
the GL account. The petty-cash finance team (Keetile, Pako, Legakwa, Tlamelo)
sets the expense account when they post it. The account is therefore nullable at
entry and required only at posting time (enforced in services.approve_voucher).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0001_initial'),
        ('petty_cash', '0007_voucher_amendment'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pettycashvoucher',
            name='expense_account',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='petty_cash_vouchers',
                to='ledger.account',
                help_text=(
                    'GL expense account this disbursement is charged to. Left '
                    'blank by the person who RAISES the voucher — the petty-cash '
                    'finance team (Keetile, Pako, Legakwa, Tlamelo) codes it when '
                    'they post it (CFO directive 2026-08-10). Required before the '
                    'voucher can be posted.'
                ),
            ),
        ),
    ]
