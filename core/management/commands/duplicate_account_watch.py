"""Nightly omni duplicate-account watchdog.

Read-only. Emails the CFO ONLY when it finds an issue; always writes a log line
(silent-when-clean so it never cries wolf). Runs from cron at midnight Botswana.

    python manage.py duplicate_account_watch            # normal nightly run (sends)
    python manage.py duplicate_account_watch --dry-run   # detect + print, never email

Detects, for staff accounts:
  HIGH   one person with 2+ ACTIVE login accounts
  HIGH   an Employee/payslip record attached to an INACTIVE account while the
         person also has an ACTIVE account (pay history stranded on a dead login)

Blank surnames are a separate one-time data-tidy, NOT a nightly alarm -- left out
on purpose to avoid crying wolf.

Matching keys (union-find over user ids): same email local-part (>=4 chars),
same first+last name (surname non-blank), same Employee.national_id (>=6).

NO company/tenant filter ON PURPOSE -- omni has ONE shared staff-login table and
the whole job is to catch a person whose duplicate spans companies (e.g.
alphadirect -> Unicoin). The report goes only to the CFO, who has all-company
access. (DeepSeek flagged "tenant isolation" 2026-08-08 -- correctly overridden
as a wrong assumption about omni's shared-login model.)
"""
import os
from collections import defaultdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.utils import timezone

try:
    from payroll.models import Employee
except Exception:  # noqa: BLE001 -- app may be absent on a given branch
    Employee = None

# Recipients are configurable (DeepSeek review 2026-08-08) so alerts survive a
# role change; default = the CFO.
_DEFAULT_TO = 'pganesharajah@alphadirect.co.bw'
_FROM = 'Alpha Direct omni <omni@alphadirect.co.bw>'


def _local_part(email):
    e = (email or '').strip().lower()
    return e.split('@', 1)[0] if '@' in e else ''


class Command(BaseCommand):
    help = 'Duplicate-account watchdog: alert the CFO when a person has 2+ active omni accounts or payslips stranded on a dead login.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='detect and print, but send no email')
        parser.add_argument('--to', default='',
                            help='override the alert recipient(s), comma-separated')

    def handle(self, *args, **opts):
        # Backward-compat with the interim host script's env switches.
        dry = opts['dry_run'] or os.environ.get(
            'DUP_WATCH_DRY', '') not in ('', '0', 'false', 'False')
        recipients = [a.strip() for a in (
            opts['to'] or os.environ.get('OMNI_DUP_WATCH_TO', _DEFAULT_TO)
        ).split(',') if a.strip()]

        high = self._find_issues()
        stamp = timezone.now().strftime('%Y-%m-%d %H:%M UTC')

        if not high:
            self.stdout.write(f'[dup-watch] {stamp} CLEAN - no duplicate-account issues')
            return

        lines = [f'omni duplicate-account watch - {stamp}', '']
        lines.append(f'NEEDS ACTION ({len(high)}):')
        lines += [f'  - {h}' for h in high]
        lines += [
            '',
            'Fix: keep the login the person actually uses, move any payslips onto it,',
            'switch off the spare. Reply to have Claude clear them.',
        ]
        body = '\n'.join(lines)
        self.stdout.write(body)

        subject = f'[omni] Duplicate-account watch: {len(high)} to action'
        if dry:
            self.stdout.write(
                f'[dup-watch] DRY RUN - would email {recipients} subject={subject!r}')
            return

        # CFO 2026-08-12: when the consolidated-emails flag is on, this alert rides
        # inside the 06:30 ops digest. Detection + the log line above still run.
        if getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False):
            self.stdout.write('[dup-watch] consolidated flag on — folded into the ops '
                              'digest; standalone email skipped.')
            return

        try:
            EmailMessage(subject=subject, body=body, from_email=_FROM,
                         to=recipients).send(fail_silently=False)
            self.stdout.write(f'[dup-watch] emailed {recipients}')
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(self.style.ERROR(
                f'[dup-watch] EMAIL FAILED: {exc!r} - the issues above are still real'))
            raise SystemExit(1)

    # ------------------------------------------------------------------ detect
    def _find_issues(self):
        """Return the list of HIGH-severity duplicate-account findings."""
        U = get_user_model()
        users = list(U.objects.all())

        emp_by_user = defaultdict(list)
        if Employee is not None:
            for e in Employee.objects.all():
                if e.user_id:
                    emp_by_user[e.user_id].append(e)

        def payslips_for(uid):
            n = 0
            for e in emp_by_user.get(uid, []):
                try:
                    n += e.payslips.count()
                except Exception as exc:  # noqa: BLE001
                    # An undercount here means the watchdog misses a stranded
                    # payslip — the one thing it exists to catch. Say so loudly.
                    self.stderr.write(
                        f'[dup-watch] WARNING: payslip count failed for '
                        f'employee {getattr(e, "pk", "?")}: {exc} — '
                        f'this figure is an UNDERCOUNT')
            return n

        def national_ids(uid):
            out = []
            for e in emp_by_user.get(uid, []):
                nid = (getattr(e, 'national_id', '') or '').strip()
                if nid and len(nid) >= 6:
                    out.append(nid)
            return out

        # union-find over user ids
        parent = {u.id: u.id for u in users}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        by_email = defaultdict(list)
        by_name = defaultdict(list)
        by_nid = defaultdict(list)
        for u in users:
            lp = _local_part(u.email)
            if lp and len(lp) >= 4:
                by_email[lp].append(u.id)
            fn = (u.first_name or '').strip().lower()
            ln = (u.last_name or '').strip().lower()
            if fn and ln:
                by_name[(fn, ln)].append(u.id)
            for nid in national_ids(u.id):
                by_nid[nid].append(u.id)

        for grp in list(by_email.values()) + list(by_name.values()) + list(by_nid.values()):
            for other in grp[1:]:
                union(grp[0], other)

        clusters = defaultdict(list)
        for u in users:
            clusters[find(u.id)].append(u)

        high = []
        for us in clusters.values():
            if len(us) < 2:
                continue
            actives = [u for u in us if u.is_active]
            label = f'{us[0].first_name} {us[0].last_name}'.strip() or '(no name)'
            if len(actives) >= 2:
                accts = ', '.join(
                    f"{u.email or '(no email)'} [id {u.id}]" for u in actives)
                high.append(f'{label}: {len(actives)} ACTIVE accounts -> {accts}')
            elif actives:
                # pay history stranded on an inactive account while a live one exists
                live_payslips = sum(payslips_for(u.id) for u in us if u.is_active)
                for u in us:
                    if not u.is_active and payslips_for(u.id) > 0:
                        # Only claim the live account is empty if it actually is.
                        # An alert that asserts something it never checked sends
                        # whoever cleans this up looking in the wrong place.
                        tail = ('while the live account has none'
                                if live_payslips == 0
                                else f'(the live account also has {live_payslips})')
                        high.append(
                            f"{label}: payslips on INACTIVE account "
                            f"{u.email or '(no email)'} [id {u.id}] {tail}")
        return high
