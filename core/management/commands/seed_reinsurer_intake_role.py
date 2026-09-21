"""core/management/commands/seed_reinsurer_intake_role.py

CFO directive 2026-09-17. The "New reinsurer" button went live and nobody
could use it: the whole reinsurance module gates on role permissions, and on
prod only 15 of 201 active staff hold any role at all — none of them in
Underwriting. The CFO named the 13 people who should be able to raise a
counterparty.

WHY A NEW ROLE RATHER THAN UNDERWRITING_MANAGER. The permission the button
needs is `uw.counterparty.submit`, which in the catalogue is only reachable
through the `uw.*` wildcard on HEAD_UNDERWRITING and UNDERWRITING_MANAGER.
Assigning either of those to 13 people would also hand out `uw.quote`,
`uw.bind` and `uw.pricing.edit` — the right to bind policies and change
pricing — which is not what was asked for and not something to grant thirteen
people as a side effect of a button. This role holds exactly two permissions:

  re.view                  See the reinsurance screens, including the
                           counterparty register they are adding to.
  uw.counterparty.submit   Raise a counterparty as a DRAFT and send it for
                           underwriting review.

`re.view` is not purely read: it also uploads KYC evidence
(document_views.UPLOAD_PERM) and sends an in-flight counterparty back to
RETURNED with a reason, from any pending stage including the CEO's
(onboarding.REQUIRED_PERMISSION[RETURNED]). Only APPROVED is protected from
that. Saying so here because this grant is the FIRST time any real person
holds `re.view`, so send-back becomes live with it — raised with the CFO.

It does NOT approve anything. Every stage of the onboarding chain after
"submitted" still needs its own permission, and the compliance sign-off on a
KYC document still needs `compliance.counterparty.approve`.

WHO GETS IT IS NOT IN THIS FILE. The names are passed in on --emails, for two
reasons: the list changes every time somebody joins or leaves Underwriting, and
needing a code change and a deploy to add one person is the exact friction that
made this button necessary in the first place; and thirteen colleagues' names
and addresses do not need to sit in the repository to grant a permission. The
current list comes from the CFO and lives with the runbook.

Idempotent. Dry-run unless --commit.

Usage:
    python manage.py seed_reinsurer_intake_role --emails a@x.bw b@x.bw
    python manage.py seed_reinsurer_intake_role --emails a@x.bw --commit
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

ROLE_CODE = 'REINSURER_INTAKE'
ROLE_NAME = 'Reinsurer Intake'
ROLE_LEVEL = 4
ROLE_DESC = (
    'Raise a reinsurance counterparty and send it for underwriting review. '
    'Can also upload KYC evidence and send an in-flight counterparty back with '
    'a reason. Cannot approve any stage, verify a document, edit a treaty or '
    'post a cession. Created 2026-09-17 because the onboarding chain had no '
    'way in and the alternative — UNDERWRITING_MANAGER — would also have '
    'granted policy binding and pricing rights.'
)
PERMISSIONS = ('re.view', 'uw.counterparty.submit')

JUSTIFICATION = ('CFO directive 2026-09-17 — named by him as the people who '
                 'raise reinsurance counterparties.')



class Command(BaseCommand):
    help = 'Seed the REINSURER_INTAKE role and assign it (CFO 2026-09-17).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Apply. Without this flag it is a dry-run.')
        parser.add_argument('--emails', nargs='+', default=[], metavar='EMAIL',
                            help='Who gets the role. Omit to create the role '
                                 'without granting it to anybody.')

    def handle(self, *args, **opts):
        from core import rbac_service
        from core.models import Permission, Role, UserRoleAssignment

        commit = opts['commit']
        emails = [e.strip() for e in opts['emails'] if e.strip()]
        report: list[str] = []

        with transaction.atomic():
            # 1. Permissions — both already exist in the catalogue. If one does
            #    not, say so loudly rather than silently creating a permission
            #    no view checks, which would look granted and do nothing.
            perms = []
            for code in PERMISSIONS:
                p = Permission.objects.filter(code=code).first()
                if p is None:
                    raise CommandError(
                        f'Permission {code!r} is not in the catalogue. Run '
                        f'seed_roles first — refusing to invent it.')
                perms.append(p)
                report.append(f'Permission {code:24} OK')

            # 2. Role
            role, was_created = Role.objects.get_or_create(
                code=ROLE_CODE,
                defaults={'name': ROLE_NAME, 'description': ROLE_DESC,
                          'level': ROLE_LEVEL, 'department': None,
                          'is_system': False, 'is_active': True},
            )
            changed = []
            for field, val in (('name', ROLE_NAME), ('description', ROLE_DESC),
                               ('level', ROLE_LEVEL)):
                if getattr(role, field) != val:
                    setattr(role, field, val)
                    changed.append(field)
            if not role.is_active:
                role.is_active = True
                changed.append('is_active')
            if changed:
                role.save(update_fields=changed)
            report.append(f'Role {ROLE_CODE} '
                          f'{"CREATED" if was_created else "EXISTS"}'
                          + (f'  updated={changed}' if changed else ''))

            # This role is deliberately exactly two permissions, so `set`
            # is right — but say what it removed. A permission somebody added
            # on the roles screen disappearing without a word is how a grant
            # gets quietly undone and nobody knows why.
            before = set(role.permissions.values_list('code', flat=True))
            role.permissions.set(perms)
            removed = sorted(before - {p.code for p in perms})
            report.append(f'Role {ROLE_CODE} permissions = '
                          + ', '.join(p.code for p in perms))
            if removed:
                report.append(f'  REMOVED (added outside this command): '
                              + ', '.join(removed))

            # 3. Assignments
            granted = existing = missing = 0
            if not emails:
                report.append('No --emails given: role only, nobody granted.')
            ambiguous = revoked = 0
            for email in emails:
                # Two active logins on one address is a REAL prod condition -
                # see core/management/commands/dedupe_login_emails.py. Picking
                # `.first()` grants the permission to whichever row wins the
                # sort, which is not a decision this command gets to make.
                matches = list(User.objects.filter(email__iexact=email,
                                                   is_active=True)[:11])
                if len(matches) > 1:
                    ambiguous += 1
                    who = ', '.join(m.username for m in matches[:10])
                    report.append(f'  AMBIGUOUS {email} — {len(matches)} active '
                                  f'accounts ({who}); granted to none. Resolve '
                                  f'the duplicate first.')
                    continue
                if not matches:
                    missing += 1
                    report.append(f'  SKIPPED  {email} — no active account')
                    continue
                user = matches[0]

                held = UserRoleAssignment.objects.filter(user=user, role=role)
                if held.filter(revoked_at__isnull=True).exists():
                    existing += 1
                    report.append(f'  ALREADY  {email}')
                    continue
                if held.filter(revoked_at__isnull=False).exists():
                    # Somebody took this away on the roles screen. Handing it
                    # back because a list in a runbook still has the name is
                    # how a deliberate revoke gets silently reversed.
                    revoked += 1
                    report.append(f'  WAS REVOKED  {email} — not re-granted. '
                                  f'Re-grant it on the roles screen if that is '
                                  f'wanted.')
                    continue

                # Through rbac_service, not a bare create: it writes the
                # AuditLog row. With the name list out of the repo, that row
                # and the justification are the ONLY record of who was granted
                # this and why.
                rbac_service.assign_role(
                    target_user=user, role=role, granted_by=None,
                    justification=JUSTIFICATION, bypass_hierarchy=True)
                granted += 1
                report.append(f'  GRANTED  {email}')

            report.append(f'{granted} granted, {existing} already held, '
                          f'{revoked} previously revoked and left alone, '
                          f'{ambiguous} ambiguous, {missing} without an account')

            if not commit:
                transaction.set_rollback(True)

        for line in report:
            self.stdout.write(line)
        self.stdout.write(
            self.style.SUCCESS('Applied.') if commit
            else self.style.WARNING('DRY RUN — re-run with --commit to apply.'))
