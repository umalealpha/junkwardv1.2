"""
hris/management/commands/link_employee_users.py

Link existing payroll.Employee rows to existing login accounts, and give each
linked employee an HRISProfile.

Why this exists (CFO directive 2026-07-31, reported by Lemogang Machola): an
employee could not apply for leave. He was told two things — "HR has not yet
linked an HRISProfile to your account" and "No leave approver has been set up
for your company yet" — and neither was the real problem. His Employee row
existed, with the right name, company and department; it simply had
`user_id = NULL`, so `_profile_for()` found nothing and
`leave_manager_employee_ids()` returned an empty set on its first line.

On 31 July 2026 that was true of **71 Employee rows**. Anyone in that state
cannot apply for leave, see a payslip, or be offered as an approver — and the
messages they get point HR at the wrong thing.

`import_m365_roster` solves the neighbouring problem (a person with no Employee
row at all). Nothing linked the two sides together, so this does.

HOW IT MATCHES — deliberately conservative. A wrong link attaches one person's
payslips and leave to another person's login, which is far worse than leaving the
row unlinked:

  1. **email**, exact and normalised — the only match trusted on its own.
  2. **full name**, normalised — used ONLY when exactly one Employee and exactly
     one User agree. Any ambiguity on either side is skipped and reported.

Never overwrites an existing link. Never touches a User that already has an
Employee. Skips shared/group mailboxes. Dry-run by default; idempotent.

    python manage.py link_employee_users                      # dry run
    python manage.py link_employee_users --commit
    python manage.py link_employee_users --only lmachola@insurance.co.bw --commit
"""
from __future__ import annotations

import re
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from hris.models import HRISProfile
from payroll.models import Employee

# Same shape as import_m365_roster's guard — a shared mailbox is not a person and
# must never be given an employee record or a leave balance.
_GROUP_RE = re.compile(
    r'(?i)department|auditor|\bteam\b|\badmin\b|\binfo\b|support|noreply|'
    r'no-reply|shared|mailbox|\bgroup\b|payroll|helpdesk|reception|accounts@|'
    r'procurement|\bparts\b|\bpeople\b|culture|recruit|careers|^hc@|^health@|'
    r'^manus@|^excoboard@|^svc-|monitor|liquidators'
)


def _norm(s) -> str:
    """Normalise a name for comparison: collapse spaces, drop dots, lowercase."""
    return re.sub(r'\s+', ' ', str(s or '').replace('.', ' ')).strip().lower()


def _user_full_name(u) -> str:
    name = f'{(u.first_name or "").strip()} {(u.last_name or "").strip()}'.strip()
    return name or (getattr(u, 'get_full_name', lambda: '')() or '').strip()


def _is_group_account(u) -> bool:
    email = (u.email or '').strip().lower()
    if not email:
        return True
    if _GROUP_RE.search(email):
        return True
    # A login with no human name attached is treated as non-human unless its
    # local part looks like a person (initial + surname).
    name = _user_full_name(u)
    return ' ' not in name and not re.fullmatch(r'[a-z]\.?[a-z-]{2,}', email.split('@')[0])


class Command(BaseCommand):
    help = 'Link existing Employee rows to existing login accounts (+ HRISProfile).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Write the links. Without it, dry-run only.')
        parser.add_argument('--only', default=None,
                            help='Restrict to one login email — used to fix a single reported case.')

    def handle(self, *args, **opts):
        commit = opts['commit']
        only = (opts['only'] or '').strip().lower() or None
        User = get_user_model()

        # TERMINATED EMPLOYEES ARE NEVER MATCH CANDIDATES (Fable 5 review
        # 2026-07-31 — both external reviewers missed this). A leaver's row often
        # carries a common name, and 13 active logins have no Employee row at all.
        # On a re-run, one of those logins sharing a name with a lone terminated
        # employee would be linked to the leaver — and that person would then see
        # the leaver's payslips, leave history and bank details. Exactly the harm
        # this command's own docstring calls worse than leaving a row unlinked.
        unlinked = list(Employee.objects.filter(user__isnull=True)
                        .exclude(status=Employee.Status.TERMINATED)
                        .exclude(is_archived=True)
                        .exclude(is_test_record=True))
        by_email: dict[str, list] = defaultdict(list)
        by_name: dict[str, list] = defaultdict(list)
        for e in unlinked:
            if e.email:
                by_email[e.email.strip().lower()].append(e)
            by_name[_norm(e.full_name)].append(e)

        # Every User already carrying an Employee is off-limits.
        taken_user_ids = set(Employee.objects.exclude(user__isnull=True)
                             .values_list('user_id', flat=True))

        candidates = User.objects.filter(is_active=True)
        if only:
            candidates = candidates.filter(email__iexact=only)

        # Name ambiguity has to be judged across ALL candidate users, not just the
        # filtered one — otherwise --only would happily make a link that the full
        # run correctly refuses as ambiguous.
        user_name_counts: dict[str, int] = defaultdict(int)
        for u in User.objects.filter(is_active=True):
            if u.id not in taken_user_ids and not _is_group_account(u):
                user_name_counts[_norm(_user_full_name(u))] += 1

        plan, skipped = [], []
        for u in candidates.order_by('id'):
            email = (u.email or '').strip().lower()

            if u.id in taken_user_ids:
                skipped.append((u, 'already linked to an employee record'))
                continue
            if _is_group_account(u):
                skipped.append((u, 'shared or service account — not a person'))
                continue

            match, how = None, ''
            if email and len(by_email.get(email, [])) == 1:
                match, how = by_email[email][0], 'email'
            elif email and len(by_email.get(email, [])) > 1:
                skipped.append((u, f'{len(by_email[email])} employee rows share this email'))
                continue
            else:
                nm = _norm(_user_full_name(u))
                emps = by_name.get(nm, [])
                if not nm:
                    skipped.append((u, 'no name on the login to match on'))
                    continue
                if len(emps) > 1:
                    skipped.append((u, f'{len(emps)} employee rows share the name "{nm}"'))
                    continue
                if user_name_counts.get(nm, 0) > 1:
                    skipped.append((u, f'{user_name_counts[nm]} logins share the name "{nm}"'))
                    continue
                if len(emps) == 1:
                    match, how = emps[0], 'name'

            if match is None:
                skipped.append((u, 'no matching employee record'))
                continue

            plan.append((u, match, how))

        # TWO LOGINS, ONE EMPLOYEE ROW. Caught by the first prod dry run:
        # `ceooffice@alphadirect.co.bw` exists as two active User rows and both
        # matched the same Employee. The write itself is safe — the
        # select_for_update re-check below means only the first would take — but
        # "whichever ran first wins" is not a decision this command should make
        # silently. Two logins claiming one person is an ambiguity, so both are
        # skipped and reported, exactly like the other ambiguous cases.
        claims: dict = defaultdict(list)
        for u, e, how in plan:
            claims[e.pk].append((u, how))
        contested = {pk for pk, us in claims.items() if len(us) > 1}
        if contested:
            for pk in contested:
                for u, _how in claims[pk]:
                    emp = next(e for _u, e, _h in plan if e.pk == pk)
                    skipped.append((u, f'{len(claims[pk])} logins claim the same employee '
                                       f'record "{emp.full_name}" — HR must say which login is theirs'))
            plan = [(u, e, how) for u, e, how in plan if e.pk not in contested]

        # ── Report ────────────────────────────────────────────────────────
        self.stdout.write(f'Unlinked employee rows      : {len(unlinked)}')
        self.stdout.write(f'Active logins considered    : {candidates.count()}')
        self.stdout.write(f'TO LINK                    : {len(plan)}')
        for u, e, how in plan:
            self.stdout.write(
                f'   + {u.email or u.id}  ->  {e.full_name}  '
                f'(company={e.company_id or "UNASSIGNED"}, dept={e.department or "-"}, '
                f'status={e.status}) [by {how}]'
            )

        real_skips = [(u, r) for u, r in skipped
                      if r not in ('shared or service account — not a person',
                                   'already linked to an employee record')]
        self.stdout.write(f'\nNot linked (needs HR)      : {len(real_skips)}')
        for u, reason in real_skips:
            self.stdout.write(f'   - {u.email or u.id}: {reason}')

        if not commit:
            self.stdout.write(self.style.WARNING('\nDRY-RUN — nothing written. Re-run with --commit.'))
            return

        linked = profiles = 0
        with transaction.atomic():
            for u, e, _how in plan:
                # Re-check under the transaction: another run or an HR edit may
                # have claimed either side since the plan was built.
                fresh = Employee.objects.select_for_update().filter(pk=e.pk, user__isnull=True).first()
                if fresh is None or Employee.objects.filter(user_id=u.id).exists():
                    continue
                fresh.user = u
                fresh.save(update_fields=['user'])
                linked += 1
                _, made = HRISProfile.objects.get_or_create(employee=fresh)
                if made:
                    profiles += 1

        self.stdout.write(self.style.SUCCESS(
            f'Linked {linked} employee record(s) to logins; created {profiles} HRIS profile(s).'))
        self.stdout.write(
            'A linked employee still needs a line manager before they can apply for leave — '
            'set it on HRIS → Amendments, or in bulk via the leave-approver upload.')
