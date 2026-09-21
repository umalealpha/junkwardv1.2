"""
hris 0011 — Seed the 'HRIS' role and assign it to the HR team.

CFO directive 2026-06-07: Unami, Dorothy and Thapelo get full HRIS editing
power via the 'HRIS' role. Every edit they make is dual-approved before it
touches the live record (see hris.amendment_service).

Idempotent: get_or_create on the role and each assignment. Users are matched
by email; any not present yet are skipped (re-run after the user exists, or
assign via the RBAC admin).
"""
from django.conf import settings
from django.db import migrations

_HRIS_TEAM_EMAILS = [
    'ubutale@alphadirect.co.bw',     # Unami Butale
    'dikgopoleng@alphadirect.co.bw',  # Dorothy Ikgopoleng
    'tmorapedi@alphadirect.co.bw',    # Thapelo Morapedi
]


def _seed(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    UserRoleAssignment = apps.get_model('core', 'UserRoleAssignment')
    User = apps.get_model('auth', 'User')

    role, _ = Role.objects.get_or_create(
        code='HRIS',
        defaults={
            'name': 'HRIS Editor',
            'description': ('Full HRIS editing power. All amendments are '
                            'dual-approved before they apply (CFO directive '
                            '2026-06-07).'),
            'level': 3,
            'department': 'hr',
            'is_system': True,
            'is_active': True,
        },
    )

    for email in _HRIS_TEAM_EMAILS:
        user = (User.objects.filter(email__iexact=email).first()
                or User.objects.filter(username__iexact=email).first())
        if not user:
            print(f"[hris.0011] user {email} not found — skipping HRIS grant")
            continue
        UserRoleAssignment.objects.get_or_create(
            user=user, role=role,
            defaults={'justification': 'HR team full HRIS access (dual-approved). '
                                       'CFO directive 2026-06-07.'},
        )


def _unseed(apps, schema_editor):
    """Deactivate the role on reverse; leave assignments for audit."""
    Role = apps.get_model('core', 'Role')
    Role.objects.filter(code='HRIS').update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0010_hrisamendment'),
        ('core', '0021_seed_currencies'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(_seed, _unseed),
    ]
