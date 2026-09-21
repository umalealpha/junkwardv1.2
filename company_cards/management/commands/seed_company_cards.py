"""
Create the four company cards (CFO 2026-08-07).

Card NUMBERS are never stored — only the last four digits, which is all a
reconciliation needs. Run with --last4 to set the real digits; without it each
card is created with 0000 as a placeholder for the CFO to correct on the
register screen.

    python manage.py seed_company_cards --commit
    python manage.py seed_company_cards --commit --last4 aiyer=4821,pbeka=7733
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

HOLDERS = [
    ('aiyer@alphadirect.co.bw',          'CEO Card'),
    ('arjuniyer@alphadirect.co.bw',      'COO Card'),
    ('pbeka@alphadirect.co.bw',          'Paul Beka Card'),
    ('pganesharajah@alphadirect.co.bw',  'CFO Card'),
]


class Command(BaseCommand):
    help = 'Create the four company credit cards (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Write (else dry-run).')
        parser.add_argument('--last4', default='',
                            help='local-part=digits pairs, comma separated.')
        parser.add_argument('--company', default='ADIC', help='Company code.')

    def handle(self, *args, **opts):
        from company_cards.models import CompanyCard
        from core.models import Company

        digits = {}
        for pair in (opts['last4'] or '').split(','):
            if '=' in pair:
                k, v = pair.split('=', 1)
                digits[k.strip().lower()] = v.strip()[:4]

        company = Company.objects.filter(code=opts['company']).first()
        made = skipped = 0
        for email, label in HOLDERS:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                self.stdout.write(self.style.WARNING(f'  ! no login for {email} — skipped'))
                continue
            last4 = digits.get(email.split('@')[0].lower(), '0000')
            existing = CompanyCard.objects.filter(holder=user).first()
            if existing:
                self.stdout.write(f'  = {label} already exists ({existing})')
                skipped += 1
                continue
            if opts['commit']:
                CompanyCard.objects.create(
                    label=label, last4=last4, holder=user, company=company)
            made += 1
            self.stdout.write(f'  + {label} ••••{last4} → {user.get_full_name() or user.username}')

        self.stdout.write(self.style.SUCCESS(
            f'{made} card(s) created, {skipped} already there.'
            + ('' if opts['commit'] else '  [DRY RUN — pass --commit]')))
