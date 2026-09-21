"""
Pair a staff record with the login that belongs to it, so self-service works.

Pramod, 5 August 2026, trying to book leave: *"Your staff record has not been linked to your
login yet, so leave cannot be applied for. Ask HR to link your employee record."* His employee
record existed, his login existed, and the email on both matched exactly — the only thing missing
was the HRIS profile that joins them. He could see his balances and could not apply.

`_profile_for()` needs BOTH links:
  · `payroll.Employee.user`  → which login owns this staff record
  · an `HRISProfile` for that Employee

15 staff were missing one or the other, which is 15 people who cannot apply for leave and have to
email HR instead. This command repairs them from the one fact we can trust — an exact email match.

    python manage.py link_staff_logins            # show what WOULD be linked, change nothing
    python manage.py link_staff_logins --commit    # do it

DELIBERATELY CAUTIOUS. It matches on the FULL email only, never on a name — a name match is how
payroll ended up with two rows for one person in the first place. Where two staff records share
one email (which happens with duplicated employee rows) it refuses both and reports them, because
guessing which one owns the login is how leave gets booked against the wrong record.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Link staff records to their logins and create the missing HRIS profiles.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Write the links. Without this, nothing changes.')
        parser.add_argument('--employee', default='',
                            help='Limit to one person (name or email), for a careful single fix.')

    def handle(self, *args, **opts):
        from django.contrib.auth import get_user_model
        from hris.models import HRISProfile
        from payroll.models import Employee

        User = get_user_model()
        commit = opts['commit']
        only = (opts['employee'] or '').strip().lower()

        # Logins by email. Inactive logins are ignored: linking a staff record to a disabled
        # account would leave the person no better off and hide the real gap.
        users_by_email = {}
        for u in User.objects.filter(is_active=True).exclude(email=''):
            users_by_email.setdefault(u.email.strip().lower(), []).append(u)

        # Staff records grouped by email, so a shared email is visible before anything is written.
        emps_by_email = defaultdict(list)
        for e in Employee.objects.all():
            if e.email:
                emps_by_email[e.email.strip().lower()].append(e)

        linked_user, made_profile, already, ambiguous, no_login, skipped = 0, 0, 0, [], [], 0

        for email, emps in sorted(emps_by_email.items()):
            users = users_by_email.get(email) or []
            for e in emps:
                if only and only not in (e.full_name or '').lower() and only != email:
                    skipped += 1
                    continue

                has_user = getattr(e, 'user_id', None) is not None
                prof = HRISProfile.objects.filter(employee=e).first()
                if has_user and prof is not None:
                    already += 1
                    continue

                if not users:
                    no_login.append(e)
                    continue
                if len(users) > 1 or len(emps) > 1:
                    # Two staff records on one email, or two logins on one email. Either way the
                    # owner is a guess, and a wrong guess books someone's leave against the wrong
                    # record. HR decides these.
                    ambiguous.append((e, email, len(emps), len(users)))
                    continue

                user = users[0]
                actions = []
                if not has_user:
                    actions.append('login link')
                    if commit:
                        e.user = user
                        e.save(update_fields=['user'])
                    linked_user += 1
                if prof is None:
                    actions.append('HRIS profile')
                    if commit:
                        HRISProfile.objects.get_or_create(employee=e)
                    made_profile += 1
                self.stdout.write(f"  {'linked ' if commit else 'would link'} "
                                  f"{e.full_name[:30]:32s} {email:34s} → {', '.join(actions)}")

        head = 'LINKED' if commit else 'WOULD LINK'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{head}: {linked_user} login link(s), {made_profile} HRIS profile(s).'))
        self.stdout.write(f'  already complete            : {already}')
        if skipped:
            self.stdout.write(f'  skipped (--employee filter) : {skipped}')

        if no_login:
            self.stdout.write(self.style.WARNING(
                f'\n  {len(no_login)} staff record(s) have no active login at all — '
                f'they need an account before self-service can work:'))
            for e in no_login[:12]:
                self.stdout.write(f'      {e.full_name[:34]:36s} {e.email or "(no email on file)"}')

        if ambiguous:
            self.stdout.write(self.style.ERROR(
                f'\n  {len(ambiguous)} REFUSED as ambiguous — HR must resolve these, because '
                f'guessing books leave against the wrong record:'))
            for e, email, n_emps, n_users in ambiguous:
                why = (f'{n_emps} staff records share this email' if n_emps > 1
                       else f'{n_users} logins share this email')
                self.stdout.write(f'      {e.full_name[:34]:36s} {email:34s} — {why}')
            self.stdout.write('      Most often this is a duplicate employee row: merge them, '
                              'then re-run.')

        if not commit:
            self.stdout.write(self.style.WARNING('\nNothing was written. Re-run with --commit.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                '\nDone. Anyone linked can now apply for leave without emailing HR.'))
