"""Rename 30 Account.name rows to match BS spec typo corrections.

Companion to commit 2c2a539 (code-side BS typo fix: Receivables /
Unearned / Severance). Oprah Mogomotsi APPROVED the combined diff
2026-06-04 09:26 BWT — "Typo diff — APPROVED. Go ahead. One commit,
exactly as shown. Code + data + reference snapshot together."

Three rename groups (30 accounts total expected — production has 24 +
3 + 3, may be fewer in lower environments):

  'Related Party Recievables'      → 'Related Party Receivables'   (24 rows on prod)
  'Unearened Premium Reserve'      → 'Unearned Premium Reserve'    (3: 205001/2/3)
  'Severence & Leave liabilities'  → 'Severance & Leave liabilities'(3: 213001/2/3)

Idempotent — if a row already carries the corrected spelling the SQL
WHERE clauses won't match it, so re-runs are no-ops. AuditLog rows are
NOT written for these mass renames (bypass AuditableMixin via raw SQL)
because we are correcting a transliteration not changing semantics —
the JE leg mapping stays the same.

Reverse migration restores the typos exactly so we can git-revert if
needed.
"""
from django.db import migrations

# (old, new) — order matters for the reverse migration
RENAMES = [
    ('Related Party Recievables',     'Related Party Receivables'),
    ('Unearened Premium Reserve',     'Unearned Premium Reserve'),
    ('Severence & Leave liabilities', 'Severance & Leave liabilities'),
]


def forwards(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    for old, new in RENAMES:
        qs = Account.objects.filter(name=old)
        n  = qs.count()
        qs.update(name=new)
        print(f'  renamed {n:>3} row(s):  {old!r} → {new!r}')


def backwards(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    for old, new in RENAMES:
        qs = Account.objects.filter(name=new)
        n  = qs.count()
        qs.update(name=old)
        print(f'  reverted {n:>3} row(s):  {new!r} → {old!r}')


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0021_je_status_returned'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
