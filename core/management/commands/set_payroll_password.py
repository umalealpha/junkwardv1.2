"""
core/management/commands/set_payroll_password.py

Sets (or clears) the per-company payroll-gate password.

Usage:
    python manage.py set_payroll_password VCM           # prompts interactively
    python manage.py set_payroll_password VCM --clear   # remove the gate
    python manage.py set_payroll_password VCM --password "abc123"  # non-interactive (avoid in shared terminals)

After setting, hitting any payroll endpoint scoped to that company
requires the password to be passed to `Company.check_payroll_password(...)`
before data is returned.
"""
import getpass

from django.core.management.base import BaseCommand, CommandError

from core.models import Company


class Command(BaseCommand):
    help = 'Set or clear the per-company payroll-gate password.'

    def add_arguments(self, parser):
        parser.add_argument('company_code', help='Short code, e.g. VCM, QIH, ADIH')
        parser.add_argument('--clear', action='store_true',
                            help='Remove the payroll gate password (open access).')
        parser.add_argument('--password', help='Set password non-interactively. '
                            'Avoid in shared terminals — leaves a shell-history entry.')

    def handle(self, *args, **opts):
        code = opts['company_code'].strip().upper()
        company = Company.objects.filter(code__iexact=code).first()
        if not company:
            available = ', '.join(Company.objects.values_list('code', flat=True).order_by('code'))
            raise CommandError(f'No Company with code {code!r}. Available: {available}')

        if opts['clear']:
            company.payroll_password_hash = ''
            company.save()
            self.stdout.write(self.style.WARNING(
                f'Cleared payroll password gate for {company.code} — {company.name}. '
                f'Payroll for this company is now open.'
            ))
            return

        raw = opts['password']
        if not raw:
            raw = getpass.getpass(f'Enter payroll password for {company.code}: ')
            confirm = getpass.getpass(f'Confirm payroll password for {company.code}: ')
            if raw != confirm:
                raise CommandError('Passwords do not match. Aborted — no change made.')
        if len(raw) < 6:
            raise CommandError('Password must be at least 6 characters.')

        company.set_payroll_password(raw)
        company.save()
        self.stdout.write(self.style.SUCCESS(
            f'Set payroll password for {company.code} — {company.name}. '
            f'Hash stored using PBKDF2; the plaintext is not retained.'
        ))
