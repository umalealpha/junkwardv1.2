# Merge migration — reconciles two parallel 0005 leaves on the core app:
#
#   0004_alter_userprofile_title
#     ├── 0005_rbac_role_permission           (RBAC layer — my hierarchy + roles)
#     └── 0005_company_payroll_password_hash  (Prathap's per-company payroll gate)
#         └── 0006_company_parent_company     (Prathap's agency relationship)
#
# Both chains were merged into main independently, leaving the migration
# graph with two leaf nodes. This empty migration depends on both leaves
# and becomes the new single tip so `manage.py migrate` is unambiguous.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_rbac_role_permission'),
        ('core', '0006_company_parent_company'),
    ]

    operations = []
