# Adds CFO-controlled classification fields to Account so the CFO can
# upload an authoritative CoA (chart of accounts) via /cfo-upload that
# tags every account with:
#   - statement_class : 'BS' or 'PNL' — drives which financial statement
#                       the account rolls up to
#   - fs_line_item    : free-text label, e.g. 'Cash and bank',
#                       'Profit and loss', 'Crossover'
#   - normal_balance_dc : 'D' or 'C' — Dr-natural or Cr-natural
#   - owner_company   : which subsidiary owns the account (informational;
#                       the unique constraint on `code` is unchanged)
#   - is_archived     : True for accounts not present in the most recent
#                       authoritative CoA upload — kept in the table for
#                       JE history but hidden from the CoA dropdowns
#
# Defaults are intentionally blank/false so the migration is non-breaking:
# existing accounts continue to work, and the CFO populates the new fields
# by uploading a CoA CSV.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0012_account_is_summary_only'),
        ('core', '0007_merge_rbac_and_company_chains'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='statement_class',
            field=models.CharField(
                max_length=3, blank=True, default='',
                choices=[('BS', 'Balance Sheet'), ('PNL', 'Profit & Loss')],
            ),
        ),
        migrations.AddField(
            model_name='account',
            name='fs_line_item',
            field=models.CharField(max_length=200, blank=True, default=''),
        ),
        migrations.AddField(
            model_name='account',
            name='normal_balance_dc',
            field=models.CharField(
                max_length=1, blank=True, default='',
                choices=[('D', 'Debit-natural'), ('C', 'Credit-natural')],
            ),
        ),
        migrations.AddField(
            model_name='account',
            name='owner_company',
            field=models.ForeignKey(
                to='core.company',
                on_delete=models.SET_NULL,
                null=True, blank=True,
                related_name='owned_accounts',
            ),
        ),
        migrations.AddField(
            model_name='account',
            name='is_archived',
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
