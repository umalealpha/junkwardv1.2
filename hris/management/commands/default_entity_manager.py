"""Give new joiners in a whole entity a default line manager (CFO 2026-08-12).

Bharath runs the UniCoin and Veritas entities, so anyone who joins them should roll
up to him without HR having to remember to set it. This ONLY fills a BLANK manager —
it never overwrites a manager a human has already chosen — so it is safe to run on a
schedule.

Real chain (kept intact): UniCoin has a team lead (Bakang Taote) who reports to
Bharath, so UniCoin joiners default to Bakang, not straight to Bharath — otherwise
Bakang's team would stop growing. Veritas people report to Bharath directly.

Configure via settings.ENTITY_DEFAULT_MANAGERS = [{'company': 'unicoin',
'head_email': '...'}, ...]; the built-in default below is used when unset.
Dry-run unless --commit.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from hris.models import HRISProfile
from payroll.models import Employee

DEFAULT_MAP = [
    {'company': 'veritas',  'head_email': 'bbalasubramanian@alphadirect.co.bw'},
    {'company': 'unicoin',  'head_email': 'btaote@insurance.co.bw'},
]


class Command(BaseCommand):
    help = 'Fill a blank line manager for new joiners in configured entities. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')

    def _head(self, email):
        return (Employee.objects.filter(user__email__iexact=email).first()
                or Employee.objects.filter(email__iexact=email).first())

    def handle(self, *args, **opts):
        rules = getattr(settings, 'ENTITY_DEFAULT_MANAGERS', None) or DEFAULT_MAP
        commit = opts['commit']
        self.stdout.write(f'mode: {"COMMIT" if commit else "DRY-RUN"}')
        total = 0
        for rule in rules:
            head = self._head(rule['head_email'])
            if head is None:
                self.stderr.write(self.style.WARNING(
                    f"  ! head {rule['head_email']} not found — skipping {rule['company']}"))
                continue
            # People in a matching company, active, with NO line manager, not the head.
            profiles = (HRISProfile.objects
                        .filter(Q(employee__company__name__icontains=rule['company']))
                        .filter(manager__isnull=True)
                        .exclude(employee__isnull=True)
                        .exclude(employee=head)
                        .exclude(employee__status=Employee.Status.TERMINATED)
                        .select_related('employee', 'employee__company'))
            n = 0
            for p in profiles:
                if commit:
                    p.manager = head
                    p.save(update_fields=['manager'])
                self.stdout.write(self.style.SUCCESS(
                    f'  {"SET " if commit else "WOULD"} {p.employee.full_name:30} -> {head.full_name}'
                    f'  ({p.employee.company.name if p.employee.company_id else "?"})'))
                n += 1
            self.stdout.write(f'  {rule["company"]}: {n} joiner(s) with no manager')
            total += n
        self.stdout.write('-' * 60)
        self.stdout.write(f'total filled: {total}')
        if not commit:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing written. Re-run with --commit.'))
