"""
core/management/commands/seed_hr_extract_account.py

CFO directive 2026-08-03. Create the service account that an `hr-extract`
API key authenticates as, so the Chief Human Capital Officer can pull her
own department's data from a Claude Code session without a browser.

Design — three independent layers, any one of which would be enough:

  1. Read-only at the FRONT DOOR. The key carries only the `hr-extract`
     scope, which sits in core.api_key_auth.READ_ONLY_SCOPES, so any
     write method is refused during authentication — before a single view
     runs, and regardless of what that view declares in permission_classes.

  2. Narrowed paths. `hr-extract` grants HRIS + employees + payslips +
     company context. It does NOT grant /api/v1/reports/, journal-entries,
     dashboard, accounts, bank-accounts, payments or invoices.

  3. A weak identity. This account is deliberately NOT a superuser, NOT an
     administrator, and holds NO role assignment. That matters: both
     is_superuser and a live `read-all` role assignment short-circuit
     ApiKeyScopePermission's checks (see its role-based bypass), which
     would defeat layer 2. Its UserProfile title is HR_MANAGER — the title
     the HRIS gates recognise as real HR (hris.leave_encash_service.is_hr,
     hris.document_access.is_hr_doc_admin) and which the 2026-07-15 access
     audit removed from UserProfile.FINANCIALS_VIEW_TITLES precisely so HR
     cannot read the accounting department's data.

Consequence of holding no `read-all` permission: this account is NOT
exempt from the company-switcher default fallback, so cross-entity pulls
must name the entity (`?company=<id>`). That is deliberate — granting
`read-all` here would re-open every GET endpoint in omni.

Pay IS in scope, per the CFO's decision of 2026-08-03 ("HR including pay").

The account cannot sign in: it is created with an unusable password and no
usable email login, so the API key is the only way to use it, and revoking
the key in Settings -> API keys removes the access completely.

Usage:
    python manage.py seed_hr_extract_account            # dry-run
    python manage.py seed_hr_extract_account --commit   # apply
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction


USERNAME  = 'hr-data-extract'
EMAIL     = 'hr-data-extract@alphadirect.co.bw'
FIRST     = 'HR Data'
LAST      = 'Extract (read-only)'
JOB_TITLE = 'HR Data Extract (read-only)'


class Command(BaseCommand):
    help = 'Create/normalise the read-only HR data-extract service account.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Apply changes (default is a dry run).')

    def handle(self, *args, **opts):
        commit = opts['commit']
        from core.models import Company, UserCompanyAccess, UserProfile

        actions: list[str] = []

        with transaction.atomic():
            user, created = User.objects.get_or_create(
                username=USERNAME,
                defaults={'email': EMAIL, 'first_name': FIRST,
                          'last_name': LAST, 'is_active': True},
            )
            actions.append(f'{"create" if created else "keep"} user {USERNAME}')

            # Never a superuser and never staff: is_superuser short-circuits
            # ApiKeyScopePermission, which would defeat the path narrowing.
            changed = []
            if user.is_superuser:
                user.is_superuser = False
                changed.append('is_superuser=False')
            if user.is_staff:
                user.is_staff = False
                changed.append('is_staff=False')
            if not user.is_active:
                user.is_active = True
                changed.append('is_active=True')
            if user.email != EMAIL:
                user.email = EMAIL
                changed.append('email')
            if user.has_usable_password():
                user.set_unusable_password()
                changed.append('password=unusable')
            if changed or created:
                user.save()
                actions.append('user flags: ' + (', '.join(changed) or 'none'))

            profile, p_created = UserProfile.objects.get_or_create(
                user=user,
                defaults={'role': UserProfile.Role.SYSTEM_API,
                          'title': UserProfile.Title.HR_MANAGER,
                          'job_title': JOB_TITLE,
                          'department': 'Human Capital',
                          'is_administrator': False,
                          'is_active': True},
            )
            actions.append(f'{"create" if p_created else "keep"} profile')

            # Normalise if anything drifted. is_administrator is the flag that
            # gives a real human (Unami) financial visibility — this service
            # identity must never carry it.
            p_changed = []
            if profile.title != UserProfile.Title.HR_MANAGER:
                profile.title = UserProfile.Title.HR_MANAGER
                p_changed.append('title=hr_manager')
            if profile.is_administrator:
                profile.is_administrator = False
                p_changed.append('is_administrator=False')
            if profile.role != UserProfile.Role.SYSTEM_API:
                profile.role = UserProfile.Role.SYSTEM_API
                p_changed.append('role=system_api')
            if not profile.is_active:
                profile.is_active = True
                p_changed.append('is_active=True')
            if p_changed:
                profile.save(update_fields=[f.split('=')[0] for f in p_changed])
                actions.append('profile: ' + ', '.join(p_changed))

            # View access to every entity — HR spans the group. can_write is
            # False on every row; writes are already refused at the front door,
            # this just means nothing in the data model claims otherwise.
            for company in Company.objects.all().order_by('name'):
                access, a_created = UserCompanyAccess.objects.get_or_create(
                    user=user, company=company,
                    defaults={'can_view': True, 'can_write': False,
                              'notes': 'HR data extract (read-only) — CFO 2026-08-03'},
                )
                if a_created:
                    actions.append(f'grant view: {company.name}')
                elif access.can_write or not access.can_view:
                    access.can_view = True
                    access.can_write = False
                    access.save(update_fields=['can_view', 'can_write'])
                    actions.append(f'normalise view-only: {company.name}')

            for line in actions:
                self.stdout.write('  ' + line)

            if not commit:
                self.stdout.write(self.style.WARNING(
                    '\nDRY RUN — rolling back. Re-run with --commit to apply.'))
                transaction.set_rollback(True)
                return

        self.stdout.write(self.style.SUCCESS(
            f'\n{USERNAME} ready. Now mint the key in omni: '
            'Settings -> API keys -> service user '
            f'"{USERNAME}", scope "hr-extract".'))
