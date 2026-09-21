from django.db import migrations

from core.migration_ops import PostgresOnlyRunSQL


# Ledger immutability — enforced at the DATABASE level (pattern: django-hordak).
# App-level guards (JournalEntry.delete(), DRF destroy endpoints) can be bypassed
# by QuerySet.delete(), the Django admin bulk action, raw SQL, or a superuser.
# A BEFORE DELETE trigger cannot: nothing short of dropping the trigger can
# delete a POSTED or REVERSED journal entry. Corrections are reversal-only.
FORWARD = r"""
CREATE OR REPLACE FUNCTION ledger_block_posted_je_delete() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('posted', 'reversed') THEN
        RAISE EXCEPTION
            'Cannot delete journal entry % (status=%). Posted entries are immutable — reverse them instead.',
            OLD.entry_number, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ledger_je_block_delete_posted ON ledger_journalentry;
CREATE TRIGGER ledger_je_block_delete_posted
    BEFORE DELETE ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_block_posted_je_delete();

CREATE OR REPLACE FUNCTION ledger_block_posted_jel_delete() RETURNS trigger AS $$
DECLARE
    parent_status text;
BEGIN
    SELECT status INTO parent_status
        FROM ledger_journalentry WHERE id = OLD.journal_entry_id;
    -- parent_status IS NULL (parent already gone, e.g. a draft cascade) -> allow.
    IF parent_status IN ('posted', 'reversed') THEN
        RAISE EXCEPTION
            'Cannot delete a line of posted/reversed journal entry %. Reverse the entry instead.',
            OLD.journal_entry_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ledger_jel_block_delete_posted ON ledger_journalentryline;
CREATE TRIGGER ledger_jel_block_delete_posted
    BEFORE DELETE ON ledger_journalentryline
    FOR EACH ROW EXECUTE FUNCTION ledger_block_posted_jel_delete();
"""

REVERSE = r"""
DROP TRIGGER IF EXISTS ledger_je_block_delete_posted ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_block_posted_je_delete();
DROP TRIGGER IF EXISTS ledger_jel_block_delete_posted ON ledger_journalentryline;
DROP FUNCTION IF EXISTS ledger_block_posted_jel_delete();
"""


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0025_jeclearing_reversed_label'),
    ]

    operations = [
        PostgresOnlyRunSQL(sql=FORWARD, reverse_sql=REVERSE),
    ]
