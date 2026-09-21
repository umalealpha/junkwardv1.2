"""Seed the "Broker Commission - Full Access" role — board item [C8].

The spec is explicit about the default: "All other users default to NO ACCESS
unless Finance explicitly grants read-only", and "Manage membership in Omni role
administration, NOT in code." So this command creates the role and puts the four
named people in it ONCE; after that, membership is changed on the roles screen
and this command is never the place to add a fifth person.

Until now the broker register answered to any commission-stage reviewer, which
is a wider group than Finance named. This closes that.

Members named by Finance on [C8]: Rose Mokgware, Keetile Mokhendo, Bokani
Makosha, and the CFO. The board item spells it "Mogkware"; the account is
rmokgware@. Matching is on the EMAIL, because at Alpha Direct the username is
not the address — deriving one from the other silently granted nothing to Rose
or Keetile on this same module, caught on prod minutes after a deploy.

Two things this command will NOT do:

  * It never re-grants someone Finance has revoked on the roles screen. A revoked
    row means a decision was taken in the UI, and a re-run that quietly undid it
    would defeat the whole point of [C8].
  * It never grants on an ambiguous email. `email` is not unique on the user
    table, so picking the first match would hand a role that decides broker pay
    to whichever duplicate account happened to win.

It REPORTS anyone who can reach the register today but is not in the new role,
so tightening the door never locks someone out quietly. Read that list before
this deploys.

Usage:
    python manage.py seed_broker_commission_role            # dry-run
    python manage.py seed_broker_commission_role --commit
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from commissions.broker_views import (BROKER_COMMISSION_PERMISSION,
                                      BROKER_COMMISSION_ROLE)

ROLE_NAME = 'Broker Commission - Full Access'
ROLE_LEVEL = 3
ROLE_DESC = (
    'Insert policies on a broker register, maintain broker aliases, set '
    'compliance, and trigger the monthly load and rollover. Created '
    '15-Sep-2026 for board item [C8]; everyone else has no access to the '
    'broker commission area unless Finance grants it here.'
)
PERMISSION_DESC = ('Full use of the broker commission register: insert policies, '
                   'maintain aliases, set compliance, run the monthly load.')
JUSTIFICATION = 'Named on board item [C8] by Finance, 14-Sep-2026.'

MEMBER_EMAILS = [
    'rmokgware@alphadirect.co.bw',
    'kmokhendo@alphadirect.co.bw',
    'bmakosha@alphadirect.co.bw',
    'pganesharajah@alphadirect.co.bw',
]


class Command(BaseCommand):
    help = 'Seed the Broker Commission - Full Access role (board item [C8]).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Apply. Without it this is a dry-run.')

    def handle(self, *args, **opts):
        from core.models import Permission, Role, UserRoleAssignment
        from core import rbac_service

        report = []
        with transaction.atomic():
            permission, created = Permission.objects.get_or_create(
                code=BROKER_COMMISSION_PERMISSION,
                defaults={'category': 'commissions',
                          'description': PERMISSION_DESC, 'is_active': True})
            report.append(f'Permission {BROKER_COMMISSION_PERMISSION} '
                          f'{"CREATED" if created else "EXISTS"}')

            role, created = Role.objects.get_or_create(
                code=BROKER_COMMISSION_ROLE,
                defaults={'name': ROLE_NAME, 'description': ROLE_DESC,
                          'level': ROLE_LEVEL, 'department': None,
                          'is_system': False, 'is_active': True})
            report.append(f'Role {BROKER_COMMISSION_ROLE} '
                          f'{"CREATED" if created else "EXISTS"}')
            role.permissions.add(permission)

            granted = []
            for email in MEMBER_EMAILS:
                matches = list(User.objects.filter(email__iexact=email)[:11])
                if len(matches) > 1:
                    shown = ', '.join(m.username for m in matches[:10])
                    report.append(
                        f'MEMBER AMBIGUOUS: {len(matches)} accounts share '
                        f'{email} ({shown}) — granted to none of them. '
                        f'Resolve the duplicate first.')
                    continue
                if not matches:
                    report.append(f'MEMBER MISSING: no account for {email}')
                    continue
                user = matches[0]
                granted.append(user.pk)

                assignments = UserRoleAssignment.objects.filter(user=user,
                                                                role=role)
                if assignments.filter(revoked_at__isnull=True).exists():
                    report.append(f'{user.username} -> {BROKER_COMMISSION_ROLE} '
                                  f'ALREADY HELD')
                elif assignments.filter(revoked_at__isnull=False).exists():
                    report.append(
                        f'{user.username} -> {BROKER_COMMISSION_ROLE} WAS REVOKED '
                        f'on the roles screen — NOT re-granted. Grant it there if '
                        f'that revoke was a mistake.')
                else:
                    # Through rbac_service, so the grant lands in the audit trail
                    # like every other one. granted_by is None: this is the seed,
                    # not a person, and that also skips the self-grant block the
                    # CFO's own row would otherwise hit.
                    rbac_service.assign_role(
                        target_user=user, role=role, granted_by=None,
                        justification=JUSTIFICATION, bypass_hierarchy=True)
                    report.append(f'{user.username} -> {BROKER_COMMISSION_ROLE} '
                                  f'GRANTED')

            # Who can reach the register today but would not once the role is
            # enforced. Printed, never auto-granted — that is Finance's call,
            # made on the roles screen.
            from commissions.access import is_reviewer
            losing = sorted(u.username for u in User.objects.filter(is_active=True)
                            .exclude(pk__in=granted).exclude(is_superuser=True)
                            if is_reviewer(u))
            if losing:
                report.append('LOSES ACCESS when the role is enforced (tell '
                              'Finance BEFORE this deploys): ' + ', '.join(losing))
            else:
                report.append('Nobody outside the new role can reach the '
                              'register today.')

            if not opts['commit']:
                transaction.set_rollback(True)

        for line in report:
            self.stdout.write(line)
        if not opts['commit']:
            self.stdout.write(self.style.WARNING('DRY RUN — re-run with --commit.'))
        else:
            self.stdout.write(self.style.SUCCESS('Applied.'))
