"""Add BankStatementLine.dedupe_key — the no-double-import guard, enforced
at the DATABASE level (not only in Python). See banking/models.py
compute_line_dedupe_key() / line_identity() for what the key is built from
and why (occurrence count, not line_number/position).

Four steps so this is safe against existing rows on prod:
  1. Add the column nullable/non-unique.
  2. Backfill every existing row with its computed fingerprint, assigning an
     occurrence index (0, 1, 2, ...) per identical (account, date, amount,
     description, reference) group, in a stable order — this guarantees
     every backfilled key is unique, so step 4 can never fail on data that
     already exists.
  3. Report (never crash, never delete) any groups that came out with more
     than one row — i.e. content that was ALREADY duplicated in the database
     before this migration existed. This is deliberately informational only:
     we cannot tell, after the fact, whether such a group is a genuine
     repeated transaction or a historical duplicate import, so every row is
     kept exactly as it was. A human reviews the reported statements.
  4. Make the column unique + required now that every row has one.
"""
import hashlib
from collections import defaultdict
from decimal import Decimal

from django.db import migrations, models

TWO_PLACES = Decimal('0.01')


def _identity(bank_account_id, transaction_date, amount, description, reference):
    return (
        str(bank_account_id),
        transaction_date.isoformat() if hasattr(transaction_date, 'isoformat')
        else str(transaction_date),
        str(Decimal(amount).quantize(TWO_PLACES)),
        (description or '').strip().lower(),
        (reference or '').strip().lower(),
    )


def _compute_key(identity_tuple, occurrence) -> str:
    parts = list(identity_tuple) + [str(occurrence)]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def backfill_dedupe_keys(apps, schema_editor):
    BankStatementLine = apps.get_model('banking', 'BankStatementLine')

    occurrence_counts = defaultdict(int)
    group_rows = defaultdict(list)   # identity -> [(statement_number, line_number), ...]

    # Stable order so re-running this migration (e.g. on a replica) assigns
    # the same occurrence index to the same physical row every time.
    qs = (
        BankStatementLine.objects
        .select_related('statement')
        .order_by('statement__bank_account_id', 'transaction_date', 'amount',
                  'description', 'reference', 'statement_id', 'line_number')
    )
    for line in qs.iterator():
        identity = _identity(
            line.statement.bank_account_id, line.transaction_date, line.amount,
            line.description, line.reference,
        )
        occurrence = occurrence_counts[identity]
        occurrence_counts[identity] = occurrence + 1
        group_rows[identity].append(
            (line.statement.statement_number, line.line_number))

        line.dedupe_key = _compute_key(identity, occurrence)
        line.save(update_fields=['dedupe_key'])

    # Step 3 — report, never crash, never delete. A group with more than one
    # row was already duplicated (by content) before this guard existed.
    dupe_groups = {k: v for k, v in group_rows.items() if len(v) > 1}
    if dupe_groups:
        extra_rows = sum(len(v) - 1 for v in dupe_groups.values())
        print(
            f'  [dedupe_key backfill] found {len(dupe_groups)} group(s) of '
            f'identical bank-statement-line content already in the database '
            f'({extra_rows} extra row(s) beyond the first in each group). '
            f'Nothing was changed or deleted — each row kept its own unique '
            f'key. Review these statements for a possible historical '
            f'duplicate import:'
        )
        # Date and amount only — never the narrative/reference. FNB
        # narratives carry counterparty names; deploy stdout is not the
        # place for customer data. Date + amount + statement#line is
        # enough for Finance to find and review the actual rows.
        for identity, rows in list(dupe_groups.items())[:20]:
            stmt_nums = ', '.join(f'{s}#{ln}' for s, ln in rows)
            print(f'    {identity[1]} {identity[2]} -> {stmt_nums}')
        if len(dupe_groups) > 20:
            print(f'    ... and {len(dupe_groups) - 20} more group(s).')


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0005_sync_model_state_2026_07_26'),
    ]

    operations = [
        migrations.AddField(
            model_name='bankstatementline',
            name='dedupe_key',
            field=models.CharField(default='', editable=False, max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_dedupe_keys, noop_reverse),
        migrations.AlterField(
            model_name='bankstatementline',
            name='dedupe_key',
            field=models.CharField(editable=False, max_length=64, unique=True),
        ),
    ]
