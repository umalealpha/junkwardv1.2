"""Migration operations shared across apps.

Kept free of model/app imports so historical migrations can import it safely.
"""
from django.db import migrations


class PostgresOnlyRunSQL(migrations.RunSQL):
    """RunSQL that only executes on PostgreSQL.

    Some migrations carry PostgreSQL-only DDL — plpgsql trigger functions
    (CREATE OR REPLACE FUNCTION ... $$), DO blocks — which SQLite cannot parse,
    so `DB_ENGINE=sqlite` (the host-side local dev fallback documented in
    settings) blew up during migrate with `near "OR": syntax error`.

    On PostgreSQL this behaves exactly like RunSQL — same statement splitting,
    same execution — so prod and CI are untouched. On any other backend the SQL
    is skipped. Only use this for DDL that enforces invariants at the DB level:
    the guarantee is then absent on SQLite, which is acceptable because SQLite
    is a local-dev-only convenience and never holds real data.
    """

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != 'postgresql':
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != 'postgresql':
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)
