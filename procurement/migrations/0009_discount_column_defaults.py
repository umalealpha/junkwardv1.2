"""Backfill DB-level defaults for legacy discount columns (CFO 2026-07-08).

Migration drift: older migrations added NOT-NULL `discount_percent` /
`discount_total` (PurchaseOrder) and `discount_amount` (PurchaseOrderLine)
columns, but the model later dropped those fields WITHOUT a column-drop
migration. The ORM then inserts without those columns, so every PO / PO-line
insert hit a NOT-NULL violation — which silently broke Claims-PO
auto-generation and manual PO creation on this database.

Fix: give each column a DB default of 0 IF IT STILL EXISTS. Idempotent, and a
no-op on a fresh database built from the current models (the columns are not
created there, so the guarded ALTERs simply skip).
"""
from django.db import migrations

from core.migration_ops import PostgresOnlyRunSQL


_COLUMNS = [
    ('procurement_purchaseorder', 'discount_percent'),
    ('procurement_purchaseorder', 'discount_total'),
    ('procurement_purchaseorderline', 'discount_amount'),
]

_FORWARD = "\n".join(
    f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = '{tbl}' AND column_name = '{col}') THEN
        EXECUTE 'ALTER TABLE {tbl} ALTER COLUMN "{col}" SET DEFAULT 0';
        EXECUTE 'UPDATE {tbl} SET "{col}" = 0 WHERE "{col}" IS NULL';
    END IF;
END $$;
"""
    for tbl, col in _COLUMNS
)


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0008_po_emailed_fields'),
    ]

    operations = [
        PostgresOnlyRunSQL(sql=_FORWARD, reverse_sql=migrations.RunSQL.noop),
    ]
