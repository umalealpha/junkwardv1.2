"""
Refresh the staff-loan scheme interest rate from the Bank of Botswana
Monetary Policy Rate (rate = MoPR + spread). Run monthly by cron.

    python manage.py refresh_staff_loan_rate

Fail-safe: if the BoB rate can't be read, the current rate is kept and Finance
is emailed to set it by hand. New loans always use the newest stored rate; the
CFO can still set a different rate on any individual loan at approval.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Refresh the staff-loan rate from the Bank of Botswana MoPR (rate = MoPR + spread).'

    def add_arguments(self, parser):
        parser.add_argument('--url', default=None, help='Override the BoB rates URL.')

    def handle(self, *args, **opts):
        from staff_loans import rates

        res = rates.refresh(url=opts.get('url') or rates.BOB_RATES_URL)

        if res['status'] == 'fetch_failed':
            try:
                from core.notifications import send_html_with_cfo_cc
                send_html_with_cfo_cc(
                    subject='Staff loan rate — could not read the Bank of Botswana rate',
                    html=('<p>Today\'s automatic staff-loan rate update could not read the '
                          'Bank of Botswana Monetary Policy Rate.</p>'
                          f'<p>The rate is unchanged at <b>{res["kept"]}%</b>. If Bank of Botswana '
                          'has changed it, set the new rate in Omni admin → Staff Loan Rates.</p>'),
                    to=[],
                )
            except Exception:  # noqa: BLE001
                pass
            self.stdout.write(self.style.WARNING(f'BoB rate unreadable — kept {res["kept"]}%.'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'Staff loan rate {res["status"]}: {res["rate"]}% '
            f'(MoPR {res["mopr"]}% + spread {res["spread"]}%).'
        ))
