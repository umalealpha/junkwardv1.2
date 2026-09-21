"""Restore the PO-discount fields (CFO 2026-07-08, second landing).

History: 0009_po_discount (PR #336/#337) added discount_percent /
discount_total / discount_amount and was APPLIED on prod. Commit 22e2a11
(claims team grades) then deleted the feature — models AND the migration
file — as accidental collateral, leaving the prod columns orphaned
(0009_discount_column_defaults patched their defaults so inserts kept
working). This migration puts the fields back into Django's migration
STATE, while the database operations are guarded ADD COLUMN IF NOT EXISTS:

  * on prod the columns already exist -> the ALTERs no-op;
  * on a fresh database (e.g. the test runner's) they are created.
"""
from decimal import Decimal

from django.db import migrations, models


_FORWARD = """
ALTER TABLE procurement_purchaseorder
    ADD COLUMN IF NOT EXISTS discount_percent numeric(5,2) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchaseorder
    ADD COLUMN IF NOT EXISTS discount_total numeric(18,2) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchaseorderline
    ADD COLUMN IF NOT EXISTS discount_amount numeric(18,2) NOT NULL DEFAULT 0;
"""

# SQLite (DB_ENGINE=sqlite, host-side local dev only) has no
# ADD COLUMN IF NOT EXISTS. It is always a fresh database — the orphaned prod
# columns this migration guards against cannot exist there — so a plain
# ADD COLUMN is both safe and required: the state_operations below put these
# fields into Django's model state, so the columns must really be created.
_FORWARD_SQLITE = """
ALTER TABLE procurement_purchaseorder
    ADD COLUMN discount_percent numeric(5,2) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchaseorder
    ADD COLUMN discount_total numeric(18,2) NOT NULL DEFAULT 0;
ALTER TABLE procurement_purchaseorderline
    ADD COLUMN discount_amount numeric(18,2) NOT NULL DEFAULT 0;
"""


def _add_discount_columns(apps, schema_editor):
    conn = schema_editor.connection
    sql = _FORWARD if conn.vendor == 'postgresql' else _FORWARD_SQLITE
    for statement in conn.ops.prepare_sql_script(sql):
        schema_editor.execute(statement, params=None)


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0011_po_line_sequence'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='purchaseorder',
                    name='discount_percent',
                    field=models.DecimalField(
                        max_digits=5, decimal_places=2, default=Decimal('0.00'),
                        help_text='Uniform % discount on every line before VAT (0-100).',
                    ),
                ),
                migrations.AddField(
                    model_name='purchaseorder',
                    name='discount_total',
                    field=models.DecimalField(
                        max_digits=18, decimal_places=2, default=Decimal('0.00'),
                        help_text='Sum of per-line discount amounts. Display only; '
                                  'subtotal is already net of it.',
                    ),
                ),
                migrations.AddField(
                    model_name='purchaseorderline',
                    name='discount_amount',
                    field=models.DecimalField(
                        max_digits=18, decimal_places=2, default=Decimal('0.00'),
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(_add_discount_columns,
                                     migrations.RunPython.noop),
            ],
        ),
    ]
