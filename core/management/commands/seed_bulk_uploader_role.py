"""
core/management/commands/seed_bulk_uploader_role.py

CFO directive 2026-05-19. Create a new BULK_UPLOADER role (level 3,
manager-grade) with three permissions:

  smart-upload  Grants access to /api/v1/smart-upload/* (parse + commit
                across every section: tb, gl, coa, ppe, vendors, ...).
  cfo-upload    Grants access to /api/v1/admin/cfo-upload-tb,
                cfo-upload-coa, cfo-upload-gl (the CFO bulk-upload
                endpoints, including override-password screen).
  read-all      Bypasses the topbar company-switcher default-fallback in
                CompanyScopedViewSetMixin. Explicit ?company= is still
                honoured; the difference is that, when no scope is
                requested, the user sees every entity instead of being
                pinned to their UserProfile.default_company.

The role is assigned to user `pganesharajah` (the temp account holding
the Manus DRF token). Idempotent: re-runs are safe.

Hyphenated permission codes are kept verbatim per the CFO's spec, even
though existing seeded perms use dotted notation (`roles.view`,
`je.approve`). The user-supplied codes are treated as a separate
namespace.

Usage:
    python manage.py seed_bulk_uploader_role            # dry-run
    python manage.py seed_bulk_uploader_role --commit   # apply
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction


PERMISSIONS = [
    ('smart-upload', 'uploads',
     'Use POST /api/v1/smart-upload/parse and /commit on any section.'),
    ('cfo-upload',   'uploads',
     'Use POST /api/v1/admin/cfo-upload-{tb,coa,gl} CFO bulk endpoints.'),
    ('read-all',     'reads',
     'Skip the topbar default-company fallback; see every entity when '
     'no ?company= filter is supplied.'),
]

ROLE_CODE  = 'BULK_UPLOADER'
ROLE_NAME  = 'Bulk Uploader'
ROLE_LEVEL = 3                    # manager-grade band
ROLE_DESC  = (
    'Cross-entity bulk-upload operator. Created 2026-05-19 to back the '
    'Manus AI re-import workflow without leaking the temporary CFO DRF '
    'token. Holders can call smart-upload + CFO bulk endpoints and skip '
    'the topbar default-company fallback.'
)
ASSIGN_TO_USERNAME = 'pganesharajah'


class Command(BaseCommand):
    help = 'Seed the BULK_UPLOADER role (CFO directive 2026-05-19).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Apply changes. Without this flag the '
                                 'command is a dry-run.')
        parser.add_argument('--assign-username', default=ASSIGN_TO_USERNAME,
                            help='Username to assign the new role to. '
                                 'Defaults to pganesharajah.')

    def handle(self, *args, **opts):
        from core.models import Permission, Role, UserRoleAssignment

        commit = opts['commit']
        assign_to = opts['assign_username']

        report = []

        with transaction.atomic():
            # 1. Permissions
            perm_objs = []
            for code, category, desc in PERMISSIONS:
                p, was = Permission.objects.get_or_create(
                    code=code,
                    defaults={'category': category, 'description': desc,
                              'is_active': True},
                )
                changed = []
                if p.category != category:
                    p.category = category; changed.append('category')
                if p.description != desc:
                    p.description = desc; changed.append('description')
                if not p.is_active:
                    p.is_active = True; changed.append('is_active')
                if changed:
                    p.save(update_fields=changed)
                report.append(f'Permission {code:14} '
                              f'{"CREATED" if was else "EXISTS"}'
                              + (f'  updated={changed}' if changed else ''))
                perm_objs.append(p)

            # 2. Role
            role, was = Role.objects.get_or_create(
                code=ROLE_CODE,
                defaults={
                    'name': ROLE_NAME,
                    'description': ROLE_DESC,
                    'level': ROLE_LEVEL,
                    'department': None,
                    'is_system': False,
                    'is_active': True,
                },
            )
            changed = []
            for field, val in (('name', ROLE_NAME),
                               ('description', ROLE_DESC),
                               ('level', ROLE_LEVEL)):
                if getattr(role, field) != val:
                    setattr(role, field, val); changed.append(field)
            if not role.is_active:
                role.is_active = True; changed.append('is_active')
            if changed:
                role.save(update_fields=changed)
            report.append(f'Role {ROLE_CODE} '
                          f'{"CREATED" if was else "EXISTS"} '
                          f'level={ROLE_LEVEL}'
                          + (f'  updated={changed}' if changed else ''))

            role.permissions.set(perm_objs)
            report.append(
                f'Role {ROLE_CODE} permissions = '
                + ', '.join(p.code for p in perm_objs)
            )

            # 3. Assignment — UserRoleAssignment uses revoked_at=None for
            # active. Pick the latest non-revoked row (if any) or create.
            user = User.objects.filter(username=assign_to).first()
            if not user:
                report.append(f'ASSIGN SKIPPED: user {assign_to!r} not found.')
            else:
                asg = (UserRoleAssignment.objects
                       .filter(user=user, role=role, revoked_at__isnull=True)
                       .order_by('-assigned_at').first())
                if asg is None:
                    asg = UserRoleAssignment.objects.create(
                        user=user, role=role,
                        justification='CFO directive 2026-05-19 — Manus '
                                      'bulk-upload window. Replaces use of '
                                      'the temporary DRF token.',
                    )
                    report.append(
                        f'UserRoleAssignment {assign_to} -> {ROLE_CODE} '
                        f'CREATED active=True'
                    )
                else:
                    report.append(
                        f'UserRoleAssignment {assign_to} -> {ROLE_CODE} '
                        f'EXISTS active=True'
                    )

            if not commit:
                transaction.set_rollback(True)

        for line in report:
            self.stdout.write(line)

        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — re-run with --commit to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Applied.'))
