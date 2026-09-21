"""
payroll/management/commands/setup_employee_users.py

Idempotent — for every Employee without a linked Django user, create:
  - a User (set_unusable_password — admin must reset before first login)
  - a UserProfile with sensible default title + role based on department

Username convention:
  first_word_of_full_name + '.' + last_word_of_full_name
  All lowercase, ASCII-only, dots-in-middle-names dropped, collisions
  resolved with a numeric suffix.

Default permissions
  Default everyone is `operations` title + `operations_staff` role
  (read-only). The CFO promotes specific staff to higher titles via
  /settings/users when ready. This avoids accidentally granting
  finance-team or approval rights to people who shouldn't have them.

Special-cased staff (per CFO direction earlier in the build session):
  - "Prathap Ganesharajah"   linked to whichever superuser already
                              exists (admin / prathap-alpha) and stays
                              CFO + administrator. NOT re-created.
  - "Arun Iyer"               C-Suite — title=executive, marked admin
                              per CFO grant.

Run:
    python manage.py setup_employee_users
    python manage.py setup_employee_users --dry-run
"""

import re
import unicodedata

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import UserProfile
from payroll.models import Employee


def _slug(text: str) -> str:
    """Lowercase ASCII slug — letters/digits only."""
    out = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode('ascii')
    out = out.lower().strip()
    out = re.sub(r'[^a-z0-9]', '', out)
    return out


def _build_username(full_name: str) -> str:
    """firstname.lastname — drops middle names, periods, etc."""
    parts = [p for p in (full_name or '').strip().split() if p and p != '.']
    parts = [p for p in parts if not re.fullmatch(r'[A-Za-z]\.', p)]   # drop "N." style initials
    if not parts:
        return ''
    if len(parts) == 1:
        return _slug(parts[0])
    first = _slug(parts[0])
    last  = _slug(parts[-1])
    if not first or not last:
        return _slug(' '.join(parts))
    return f'{first}.{last}'


def _ensure_unique(username: str) -> str:
    """Append -2, -3, ... until the username is free."""
    base = username
    n = 1
    while User.objects.filter(username=username).exists():
        n += 1
        username = f'{base}-{n}'
    return username


# (department-name -> default UserProfile title + role)
DEPT_DEFAULTS = {
    'Finance & Planning': (UserProfile.Title.ACCOUNTANT,  UserProfile.Role.ACCOUNTANT),
    'Senior Management':  (UserProfile.Title.EXECUTIVE,   UserProfile.Role.EXECUTIVE),
    'C-Suite':            (UserProfile.Title.EXECUTIVE,   UserProfile.Role.EXECUTIVE),
    'Executive':          (UserProfile.Title.EXECUTIVE,   UserProfile.Role.EXECUTIVE),
    # Everything else falls through to operations / operations_staff.
}


def _defaults_for(employee: Employee):
    title, role = DEPT_DEFAULTS.get(
        employee.department,
        (UserProfile.Title.OPERATIONS, UserProfile.Role.OPERATIONS_STAFF),
    )
    is_admin = False

    # Per-CFO overrides
    name = (employee.full_name or '').strip().lower()
    if name == 'arun iyer':
        title    = UserProfile.Title.EXECUTIVE
        is_admin = True
    if name == 'prathap ganesharajah':
        title    = UserProfile.Title.CFO
        is_admin = True

    return title, role, is_admin


class Command(BaseCommand):
    help = 'Create Django users + UserProfiles for every Employee that lacks one.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would happen without writing.')
        parser.add_argument('--hr-approved-by', default='',
                            help='Email of the HR person who approved these logins. Required to '
                                 'write (CFO rule 19-Sep-2026: no Omni login without HR consent).')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        if not dry and not (opts.get('hr_approved_by') or '').strip():
            self.stderr.write('Refusing to create logins without --hr-approved-by <HR email> '
                              '(no Omni login without HR consent). Showing a dry run instead.')
            dry = True
        created_users  = 0
        created_profiles = 0
        linked_existing  = 0
        linked_by_email  = 0
        skipped          = 0
        cfo_linked       = False

        # Only eligible active real staff get a login. A terminated, archived,
        # or QA/test Employee must never be provisioned — a dormant account for
        # a leaver is an access-control risk, and test rows are not people.
        eligible = (Employee.objects
                    .filter(user__isnull=True)
                    .exclude(status=Employee.Status.TERMINATED)
                    .exclude(is_archived=True)
                    .exclude(is_test_record=True))

        # Phase A — link Employees to existing Users by email match. Cheap,
        # idempotent, never creates duplicates of the 22 pre-existing Users.
        with_email_no_user = (eligible
                              .exclude(email='')
                              .exclude(email__isnull=True))
        for emp in with_email_no_user:
            existing = User.objects.filter(email__iexact=emp.email.strip()).first()
            if existing is None:
                continue
            if dry:
                self.stdout.write(
                    f'  ↪ [dry] {emp.full_name:<35} email-matched → user {existing.username!r}'
                )
                linked_by_email += 1
                continue
            emp.user = existing
            emp.save(update_fields=['user', 'updated_at'])
            linked_by_email += 1
            self.stdout.write(self.style.SUCCESS(
                f'  ↪ {emp.full_name:<35} email-matched → user {existing.username!r}'
            ))

        # Phase B — create Users for the remaining unlinked Employees.
        for emp in eligible:
            target_username = _build_username(emp.full_name)
            if not target_username:
                self.stderr.write(self.style.WARNING(
                    f'  ! Skipped {emp.full_name!r}: cannot build username.'
                ))
                skipped += 1
                continue

            # Special case: Prathap — link to existing superuser if present.
            if (emp.full_name or '').strip().lower() == 'prathap ganesharajah':
                existing = (
                    User.objects.filter(username='prathap').first()
                    or User.objects.filter(username='prathap-alpha').first()
                    or User.objects.filter(is_superuser=True).order_by('id').first()
                )
                if existing is not None:
                    if not dry:
                        emp.user = existing
                        emp.save()
                    linked_existing += 1
                    cfo_linked = True
                    self.stdout.write(self.style.SUCCESS(
                        f'  ↪ Prathap Ganesharajah linked to existing user {existing.username!r}'
                    ))
                    # Make sure their profile is up to scratch
                    profile = getattr(existing, 'profile', None)
                    if profile is None and not dry:
                        UserProfile.objects.create(
                            user=existing,
                            role=UserProfile.Role.FINANCE_ADMIN,
                            title=UserProfile.Title.CFO,
                            department=emp.department or 'Finance',
                            is_administrator=True,
                        )
                        created_profiles += 1
                    elif profile is not None and not dry:
                        changed = False
                        if profile.title != UserProfile.Title.CFO:
                            profile.title = UserProfile.Title.CFO; changed = True
                        if not profile.is_administrator:
                            profile.is_administrator = True; changed = True
                        if changed:
                            profile.save()
                    continue

            username = _ensure_unique(target_username)
            title, role, is_admin = _defaults_for(emp)

            if dry:
                self.stdout.write(
                    f'  + [dry] {username:<35}  {emp.full_name:<35}  '
                    f'title={title}  role={role}  admin={is_admin}'
                )
                continue

            user = User.objects.create(
                username=username,
                email=emp.email or '',
                first_name=(emp.full_name or '').split()[0] if emp.full_name else '',
                last_name=(emp.full_name or '').split()[-1] if emp.full_name and ' ' in emp.full_name else '',
                is_active=True,
            )
            user.set_unusable_password()  # admin must reset before login
            user.save()
            created_users += 1

            UserProfile.objects.create(
                user=user,
                role=role,
                title=title,
                department=emp.department or '',
                is_administrator=is_admin,
            )
            created_profiles += 1

            emp.user = user
            emp.save()

            self.stdout.write(self.style.SUCCESS(
                f'  + {username:<35}  {emp.full_name:<35}  title={title}  admin={is_admin}'
            ))

        self.stdout.write(self.style.SUCCESS(
            f'\n{"DRY RUN " if dry else ""}'
            f'Users created: {created_users}, '
            f'profiles created: {created_profiles}, '
            f'email-matched links: {linked_by_email}, '
            f'name-matched links: {linked_existing}, '
            f'skipped: {skipped}'
        ))
        if not cfo_linked and Employee.objects.filter(full_name__iexact='Prathap Ganesharajah').exists():
            self.stdout.write(self.style.WARNING(
                '! No existing superuser found to link Prathap to. '
                'Create one with createsuperuser or run setup_initial_data first.'
            ))

        # Important reminder for the CFO
        self.stdout.write(self.style.WARNING(
            '\nIMPORTANT: All new users have UNUSABLE passwords. '
            'Reset via Django admin or /api/v1/user-profiles/{id}/ before they can log in. '
            'Default title is "operations" — promote specific staff to '
            'Accountant / Finance Manager / Financial Controller via /settings/users.'
        ))
