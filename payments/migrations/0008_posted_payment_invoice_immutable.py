from django.db import migrations

from core.migration_ops import PostgresOnlyRunSQL


# Extend DB-level immutability (see ledger.0026) to money records: a confirmed
# payment or a posted invoice has GL entries, so it must never be hard-deleted
# by anyone (QuerySet.delete / admin bulk / raw SQL / superuser). Only DRAFTs —
# which have no ledger impact — stay deletable, mirroring the DRF destroy guards.
FORWARD = r"""
CREATE OR REPLACE FUNCTION block_nondraft_payment_delete() RETURNS trigger AS $$
BEGIN
    IF OLD.status <> 'draft' THEN
        RAISE EXCEPTION
            'Cannot delete payment % (status=%). Only draft payments can be deleted; reset to draft or reverse it.',
            OLD.id, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS payments_block_nondraft_delete ON payments_payment;
CREATE TRIGGER payments_block_nondraft_delete
    BEFORE DELETE ON payments_payment
    FOR EACH ROW EXECUTE FUNCTION block_nondraft_payment_delete();

CREATE OR REPLACE FUNCTION block_nondraft_invoice_delete() RETURNS trigger AS $$
BEGIN
    IF OLD.status <> 'draft' THEN
        RAISE EXCEPTION
            'Cannot delete invoice % (status=%). Only draft invoices can be deleted; reverse it instead.',
            OLD.id, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS billing_block_nondraft_invoice_delete ON billing_invoice;
CREATE TRIGGER billing_block_nondraft_invoice_delete
    BEFORE DELETE ON billing_invoice
    FOR EACH ROW EXECUTE FUNCTION block_nondraft_invoice_delete();

CREATE OR REPLACE FUNCTION block_nondraft_invoiceline_delete() RETURNS trigger AS $$
DECLARE
    parent_status text;
BEGIN
    SELECT status INTO parent_status FROM billing_invoice WHERE id = OLD.invoice_id;
    -- parent_status IS NULL (parent gone, e.g. a draft cascade) -> allow.
    IF parent_status IS NOT NULL AND parent_status <> 'draft' THEN
        RAISE EXCEPTION
            'Cannot delete a line of non-draft invoice %. Reverse the invoice instead.',
            OLD.invoice_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS billing_block_nondraft_invoiceline_delete ON billing_invoiceline;
CREATE TRIGGER billing_block_nondraft_invoiceline_delete
    BEFORE DELETE ON billing_invoiceline
    FOR EACH ROW EXECUTE FUNCTION block_nondraft_invoiceline_delete();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS payments_block_nondraft_delete ON payments_payment;
DROP FUNCTION IF EXISTS block_nondraft_payment_delete();
DROP TRIGGER IF EXISTS billing_block_nondraft_invoice_delete ON billing_invoice;
DROP FUNCTION IF EXISTS block_nondraft_invoice_delete();
DROP TRIGGER IF EXISTS billing_block_nondraft_invoiceline_delete ON billing_invoiceline;
DROP FUNCTION IF EXISTS block_nondraft_invoiceline_delete();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0007_payment_tier_approval'),
        ('billing', '0009_billapprovalpolicy_tiers'),
        ('ledger', '0026_posted_je_immutable_triggers'),
    ]

    operations = [
        PostgresOnlyRunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
