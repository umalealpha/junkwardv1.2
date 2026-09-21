"""
core/management/commands/dedupe_login_emails.py

Retire duplicate login accounts that share one email address.

Why this exists (CFO directive 31 Jul 2026, raised by Unami Butale): Modiri Fofo
Katai could not be linked to her payroll record because `ceooffice@alphadirect.co.bw`
existed as TWO active logins — ids 400 and 414, created 80 milliseconds apart on
9 July 2026, both named Modiri/Katai, neither ever signed into. `link_employee_users`
correctly refused to guess which one was theirs, so their July payslip stayed on a
record with no login and they could not see it.

Unami's read was that the role mailbox and a personal address were fighting. They
are not — there is NO personal address on record at all (no M365 roster entry), and
both logins carry the same `ceooffice@` address. It is a duplicate-insert, not an
identity clash.

On 31 Jul 2026 this was the ONLY duplicate-email pair in the system, so this is a
one-off cleanup rather than a recurring sweep — but it is written as a command so the
next one is a one-liner and leaves a trail.

SAFETY:
  * Dry-run by default.
  * REFUSES to retire a login that has EVER been used (`last_login` set) — a used
    account is somebody's real account, whatever the duplication says.
  * REFUSES to retire a login that carries an Employee, an HRIS profile, or any
    reverse relation — that is a merge, not a dedupe, and merge_employee_records
    is the tool for it. `--force-id` overrides that ONE id after a human has read
    the attachments, and re-prints them so the override is recorded. Only the login
    is switched off; every attached record keeps its original attribution.
  * Keeps the account with HISTORY, not the lowest id. The first dry run proved why:
    for `ceooffice@` the LOWER id (400) was the empty shell and the HIGHER id (414)
    carried 4 audit entries, a UserProfile, an OnlinePresence and a journal entry it
    had created. "Oldest wins" would have retired the account actually in use.
    Order of preference: has signed in → most records attached → lowest id.
  * Nothing is deleted: the row stays, `is_active=False`, so any audit trail
    pointing at it survives.

    python manage.py dedupe_login_emails                        # dry run, all dups
    python manage.py dedupe_login_emails --email ceooffice@alphadirect.co.bw --commit
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count


class Command(BaseCommand):
    help = 'Deactivate duplicate login accounts sharing one email (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--email', default=None, help='Restrict to one address.')
        parser.add_argument('--commit', action='store_true', help='Write. Without it, dry-run.')
        parser.add_argument('--force-id', action='append', type=int, default=[],
                            help='Deactivate this login id even though records are attached. '
                                 'Only after a human has READ those records. The attachments '
                                 'are re-printed so the override is on the record.')

    def handle(self, *args, **opts):
        User = get_user_model()
        commit = opts['commit']

        qs = User.objects.exclude(email='')
        if opts['email']:
            qs = qs.filter(email__iexact=opts['email'])

        dup_emails = [r['email'] for r in qs.values('email')
                      .annotate(n=Count('id')).filter(n__gt=1)]
        if not dup_emails:
            self.stdout.write('No duplicate login emails found.')
            return

        def attachments(u) -> list[str]:
            out = []
            for rel in User._meta.related_objects:
                try:
                    n = rel.related_model.objects.filter(**{rel.field.name: u}).count()
                except Exception:                          # noqa: BLE001
                    continue
                if n:
                    out.append(f'{rel.related_model.__name__}.{rel.field.name}={n}')
            return out

        plan, refused = [], []
        for email in dup_emails:
            rows = list(User.objects.filter(email=email).order_by('id'))
            att = {u.id: attachments(u) for u in rows}
            # The real account is the one with history — NOT the oldest row.
            rows.sort(key=lambda u: (u.last_login is not None, len(att[u.id]), -u.id), reverse=True)
            keep, others = rows[0], rows[1:]
            self.stdout.write(f'\n{email}  —  {len(rows)} logins: {sorted(u.id for u in rows)}')
            self.stdout.write(f'   KEEP id={keep.id}  last_login={keep.last_login}  '
                              f'attached={att[keep.id] or "nothing"}')

            for u in others:
                if u.last_login is not None:
                    refused.append((u, f'has signed in before ({u.last_login}) — this is a real account'))
                    continue
                attached = att[u.id]
                if attached and u.id not in opts['force_id']:
                    refused.append((u, f'has records attached ({", ".join(attached)}) — that is a '
                                       f'merge, not a dedupe'))
                    continue
                if attached:
                    # Explicit, evidenced override. Only the LOGIN is switched off —
                    # every attached record keeps its original attribution, so the
                    # audit trail and any posted entry stay exactly as they were.
                    self.stdout.write(self.style.WARNING(
                        f'   FORCED id={u.id} — records stay attributed to it: '
                        f'{", ".join(attached)}'))
                plan.append(u)
                why = 'forced — see the attachments listed above' if attached \
                    else 'never signed in, nothing attached'
                self.stdout.write(f'   RETIRE id={u.id} — {why}')

        for u, why in refused:
            self.stdout.write(self.style.WARNING(f'   REFUSED id={u.id}: {why}'))

        self.stdout.write(f'\nTO DEACTIVATE: {len(plan)}   REFUSED: {len(refused)}')

        if not commit:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing written. Re-run with --commit.'))
            return

        with transaction.atomic():
            for u in plan:
                u.is_active = False
                u.save(update_fields=['is_active'])

        self.stdout.write(self.style.SUCCESS(
            f'Deactivated {len(plan)} duplicate login(s). Rows kept (not deleted) so any '
            f'audit trail pointing at them survives.'))
