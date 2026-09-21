"""Rejoin the two hris migration branches (2026-08-12).

Two features landed on main within minutes of each other, each adding an 0065:
the disciplinary natural-justice fields and the co-rating additional-reviewer
change. Each branch was individually valid and CI was green on both, because CI
validates a BRANCH — the second leaf only exists after the merge, and nothing
re-ran the migration-graph check on the merged result. Django then refuses to
migrate with two leaf nodes, so the backend crash-looped on startup in prod.

No operations: this only joins the graph back into a single line.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0065_alter_corating_rater_role_additionalreviewer'),
        ('hris', '0066_disciplinary_unheard_issue'),
    ]

    operations = [
    ]
