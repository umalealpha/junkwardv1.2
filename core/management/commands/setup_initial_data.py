"""
Management command: setup_initial_data

Creates the seed data required for the system to function:
  - Currencies  : BWP, USD, ZAR
  - Tax rates   : VAT Standard 14%, VAT Zero 0%, VAT Exempt
  - Admin user  : username=admin, password from DJANGO_SUPERUSER_PASSWORD
                  (unusable password if that env var is unset — never a default)
"""


from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from core.models import Company, Currency, TaxRate, UserProfile
from django.utils import timezone


class Command(BaseCommand):
    help = 'Populate initial reference data (currencies, tax rates, admin user).'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('Setting up initial data…'))
        self._create_currencies()
        self._create_tax_rates()
        self._create_admin_user()
        self._create_companies()
        self.stdout.write(self.style.SUCCESS('\nInitial data setup complete.'))

    # ------------------------------------------------------------------
    # Companies / subsidiaries
    # ------------------------------------------------------------------

    def _create_companies(self):
        self.stdout.write('\n  Companies:')
        companies = [
            # Insurance carrier (default) — already in production.
            {
                'code': 'ADI', 'name': 'Alpha Direct Insurance',
                'legal_name': 'Alpha Direct Insurance (Pty) Ltd',
                'country': 'BW', 'is_default': True,
                'base_currency_id': 'BWP',
            },
            # ----- Group trading / non-insurance subsidiaries -----
            # Mirrors the company list configured in odoo.alphadirect.co.bw
            # (10 non-ADIC entities). All treated as trading / basic companies
            # per CFO directive — only ADI carries the insurance accounting
            # overlay (GWP, UPR, IBNR). Codes match the HRIS UI's company
            # switcher.
            {
                'code': 'ADIPL', 'name': 'Alpha Direct Insurtech',
                'legal_name': 'Alpha Direct Insurtech Pte Ltd',
                'country': 'SG', 'base_currency_id': 'SGD',
            },
            {
                'code': 'ADSA', 'name': 'Alpha Direct South Africa',
                'legal_name': 'Alpha Direct South Africa (Pty) Ltd',
                'country': 'ZA', 'base_currency_id': 'ZAR',
            },
            {
                'code': 'AIZ', 'name': 'Alpha Insurtech Zambia',
                'legal_name': 'Alpha Insurtech Zambia (Pvt) Ltd',
                'country': 'ZM', 'base_currency_id': 'ZMW',
            },
            {
                'code': 'ADIL', 'name': 'Alpha Direct Life',
                'legal_name': 'Alpha Direct Life Insurance Pty Ltd',
                'country': 'BW', 'base_currency_id': 'BWP',
            },
            {
                'code': 'ADRG', 'name': 'ADRisk Global',
                'legal_name': 'ADRISK Global Solutions Private Limited',
                'country': 'BW', 'base_currency_id': 'BWP',
            },
            {
                'code': 'RSA', 'name': 'Risk Software Africa',
                'legal_name': 'Risk Software Africa Pty Ltd',
                'country': 'BW', 'base_currency_id': 'BWP',
            },
            {
                'code': 'QIH', 'name': 'Quantum Insurance Holdings',
                'legal_name': 'Quantum Insurance Holdings',
                'country': 'BW', 'base_currency_id': 'BWP',
            },
            {
                'code': 'VCM', 'name': 'Veritas Capital',
                'legal_name': 'Veritas Capital Management Pty Ltd',
                'country': 'BW', 'base_currency_id': 'BWP',
            },
            {
                'code': 'UNI', 'name': 'Unicoin',
                'legal_name': 'Unicoin Proprietary Limited',
                'country': 'BW', 'base_currency_id': 'BWP',
            },
            {
                'code': 'GCX', 'name': 'Gaborone Coin Exchange',
                'legal_name': 'Gaborone Coin Exchange Proprietary Limited',
                'country': 'BW', 'base_currency_id': 'BWP',
            },
        ]
        for data in companies:
            obj, created = Company.objects.get_or_create(
                code=data['code'], defaults=data,
            )
            status_lbl = 'created' if created else 'already exists'
            self.stdout.write(f"    {obj.code} ({obj.name}): {status_lbl}")

        # Backfill existing financial rows to the default company so legacy
        # data has a home. Idempotent — only updates rows where company is null.
        default = Company.get_default()
        if default:
            from billing.models import Invoice
            from payments.models import Payment
            from ledger.models import JournalEntry
            inv_n = Invoice.objects.filter(company__isnull=True).update(company=default)
            pay_n = Payment.objects.filter(company__isnull=True).update(company=default)
            je_n  = JournalEntry.objects.filter(company__isnull=True).update(company=default)
            self.stdout.write(
                f"    backfilled to {default.code}: "
                f"{inv_n} invoices, {pay_n} payments, {je_n} journal entries"
            )

    # ------------------------------------------------------------------
    # Currencies
    # ------------------------------------------------------------------

    def _create_currencies(self):
        self.stdout.write('\n  Currencies:')
        currencies = [
            {'code': 'BWP', 'name': 'Botswana Pula',      'symbol': 'P',   'decimal_places': 2},
            {'code': 'USD', 'name': 'US Dollar',           'symbol': '$',   'decimal_places': 2},
            {'code': 'ZAR', 'name': 'South African Rand',  'symbol': 'R',   'decimal_places': 2},
            {'code': 'INR', 'name': 'Indian Rupee',        'symbol': '₹',   'decimal_places': 2},
            {'code': 'ZMW', 'name': 'Zambian Kwacha',      'symbol': 'ZK',  'decimal_places': 2},
            {'code': 'SGD', 'name': 'Singapore Dollar',    'symbol': 'S$',  'decimal_places': 2},
        ]
        for data in currencies:
            _, created = Currency.objects.get_or_create(code=data['code'], defaults=data)
            status = self.style.SUCCESS('created') if created else 'already exists'
            self.stdout.write(f"    {data['code']} ({data['name']}): {status}")

    # ------------------------------------------------------------------
    # Tax Rates
    # ------------------------------------------------------------------

    def _create_tax_rates(self):
        self.stdout.write('\n  Tax Rates:')
        today = timezone.localdate()
        tax_rates = [
            {
                'tax_code':       'VAT_STD',
                'name':           'Standard Rate 14%',
                'rate':           '14.00',
                'is_active':      True,
                'effective_from': today,
                'effective_to':   None,
            },
            {
                'tax_code':       'VAT_ZERO',
                'name':           'Zero Rated',
                'rate':           '0.00',
                'is_active':      True,
                'effective_from': today,
                'effective_to':   None,
            },
            {
                'tax_code':       'VAT_EXEMPT',
                'name':           'Exempt',
                'rate':           None,
                'is_active':      True,
                'effective_from': today,
                'effective_to':   None,
            },
        ]
        for data in tax_rates:
            _, created = TaxRate.objects.get_or_create(
                tax_code=data['tax_code'], defaults=data,
            )
            status = self.style.SUCCESS('created') if created else 'already exists'
            self.stdout.write(f"    {data['tax_code']} ({data['name']}): {status}")

    # ------------------------------------------------------------------
    # Admin user
    # ------------------------------------------------------------------

    def _create_admin_user(self):
        self.stdout.write('\n  Admin user:')
        user, created = User.objects.get_or_create(
            username='admin',
            defaults={
                'email':        'admin@alphadirect.co.bw',
                'first_name':   'System',
                'last_name':    'Admin',
                'is_staff':     True,
                'is_superuser': True,
            },
        )
        if created:
            # SECURITY (2026-07-17 audit): never seed a hardcoded password.
            # Take it from DJANGO_SUPERUSER_PASSWORD; if that is unset, leave
            # the account with an UNUSABLE password (set_unusable_password) so
            # it cannot be logged into until an admin sets one deliberately.
            import os as _os
            _pw = _os.environ.get('DJANGO_SUPERUSER_PASSWORD', '').strip()
            if _pw:
                user.set_password(_pw)
            else:
                user.set_unusable_password()
            user.save()
            UserProfile.objects.create(
                user=user,
                role=UserProfile.Role.FINANCE_ADMIN,
                title=UserProfile.Title.SYSTEM_API,
                department='Finance',
                is_administrator=True,
            )
            _pw_note = ('password: from DJANGO_SUPERUSER_PASSWORD'
                        if _pw else 'password: UNUSABLE until an admin sets one')
            self.stdout.write(
                f"    admin: {self.style.SUCCESS('created')} "
                f"(title=System/API, is_administrator=True, {_pw_note})"
            )
        else:
            # Backfill on existing admin if upgrading an older deployment
            profile, _ = UserProfile.objects.get_or_create(
                user=user,
                defaults={
                    'role': UserProfile.Role.FINANCE_ADMIN,
                    'title': UserProfile.Title.SYSTEM_API,
                    'department': 'Finance',
                    'is_administrator': True,
                },
            )
            # The system 'admin' account is NOT a CFO — only the real CFO holds
            # that title (CFO directive 2026-06-29). It keeps is_administrator
            # (+ superuser) so it retains full operational power without
            # masquerading as a finance officer. Only fill a BLANK title.
            changed = False
            if not profile.title:
                profile.title = UserProfile.Title.SYSTEM_API
                changed = True
            if not profile.is_administrator:
                profile.is_administrator = True
                changed = True
            if changed:
                profile.save()
                self.stdout.write(
                    f"    admin: {self.style.WARNING('upgraded')} (title -> System/API, is_administrator -> True)"
                )
            else:
                self.stdout.write('    admin: already exists')
