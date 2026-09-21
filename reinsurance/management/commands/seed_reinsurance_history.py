"""seed_reinsurance_history — load the LOCKED 11-year treaty-performance dataset.

CFO directive 2026-08-14. Idempotent: creates the singleton once and NEVER
clobbers it on re-run (so a re-deploy can't wipe an amended dataset). Pass
--force to re-seed the base figures (still snapshots nothing — use the UI amend
for real edits).

Figures are the verified cumulative 11-year (UWY 2014/15–2025/26, as at
31 Mar 2026) totals from the CFO's proportional & non-proportional treaty-stats
workbooks. Reproduced faithfully; the dashboard reads THESE, not the Excel.
"""
from django.core.management.base import BaseCommand

from reinsurance.models import ReinsuranceHistory

# Per-underwriting-year loss ratios (%) for the sparklines, from the
# proportional workbook's "11-Year Treaty Statistics" sheet.
UWY = ['14/15', '15/16', '16/17', '17/18', '18/19', '19/20',
       '20/21', '21/22', '22/23', '23/24', '24/25', '25/26']

DATASET = {
    'as_at': '2026-03-31',
    'currency': 'BWP',
    'title': '11-Year Reinsurance Treaty Performance',
    'subtitle': 'Underwriting years 2014/15 – 2025/26 · cumulative · as at 31 March 2026',
    'large_loss_note': ("The single largest loss in the book — the Nata Lodge claim (UWY 2019/20) — "
                        "distorts the non-motor and general figures. The 'excl. large loss' columns "
                        "strip its effect to show the underlying result."),
    # Proportional treaties (quota share & surplus)
    'proportional': [
        {'key': 'cvqs', 'name': 'Commercial Vehicle Quota Share',
         'status': 'Discontinued (from UWY 2023/24)',
         'premium': 64637650.9, 'commission': 16406792.2, 'incurred': 61494953.0,
         'net': -13264094.3, 'lr': 100,
         'net_ex_ll': None, 'lr_ex_ll': None,
         'note': 'Claims ≈ premium over its life; the loss-making book, correctly closed.'},
        {'key': 'gqs', 'name': 'General Quota Share',
         'premium': 48951699.9, 'commission': 16229073.4, 'incurred': 32143471.5,
         'net': 579155.0, 'lr': 66,
         'net_ex_ll': 5060565.1, 'lr_ex_ll': 60,
         'note': 'Steady, mildly profitable; clearly positive once the large loss is set aside.'},
        {'key': 'fire', 'name': 'Fire & Engineering Surplus',
         'premium': 29782537.5, 'commission': 8473807.1, 'incurred': 9903682.3,
         'net': 11405048.1, 'lr': 33,
         'net_ex_ll': 12531449.4, 'lr_ex_ll': 30,
         'note': 'Best-performing cover — low loss ratio, largest profit; renews on strong terms.'},
        {'key': 'mqs', 'name': 'Motor Quota Share',
         'status': 'New J.B. Boda placement (from UWY 2024/25)',
         'premium': 94083244.4, 'commission': 31972374.6, 'incurred': 64276002.4,
         'net': -2165132.6, 'lr': 68,
         'net_ex_ll': -1581751.0, 'lr_ex_ll': 68,
         'note': 'Two years of data; running a normal ~68% motor loss ratio.'},
    ],
    # Non-proportional treaties (excess of loss)
    'nonproportional': [
        {'key': 'motorxl', 'name': 'Motor Excess of Loss',
         'detail': 'Risk & CAT, 4 layers',
         'premium': 14767230.0, 'incurred': 1544371.0, 'net': 13222859.0, 'lr': 10,
         'net_ex_ll': None, 'lr_ex_ll': None,
         'note': 'Few losses ever breach the layers — cheap, reliable, highly profitable.'},
        {'key': 'nonmotorxl', 'name': 'Non-Motor Excess of Loss',
         'detail': 'Risk & CAT, layered',
         'premium': 20058280.3, 'incurred': 12537415.7, 'net': 7520864.5, 'lr': 62,
         'net_ex_ll': 14086436.8, 'lr_ex_ll': 30,
         'note': 'Carries the Nata Lodge loss; strongly profitable once that shock is excluded.'},
    ],
    # Whole-programme proportional totals (incl. large losses)
    'totals': {'premium': 237455132.7, 'commission': 73082047.3, 'incurred': 167818109.2,
               'net': -3445023.8, 'lr': 68,
               'net_ex_ll': 2746169.1, 'lr_ex_ll': 68},
    # Per-year loss-ratio series (%) for the mini-charts
    'lr_series': {
        'cvqs': [{'uwy': u, 'lr': v} for u, v in zip(
            UWY, [160, 150, 100, 110, 60, 50, 90, 60, 110, 110, 120, 0])],
        'gqs': [{'uwy': u, 'lr': v} for u, v in zip(
            UWY, [110, 70, 50, 40, 40, 180, 30, 40, 70, 80, 20, 80])],
        'fire': [{'uwy': u, 'lr': v} for u, v in zip(
            UWY, [0, 20, 10, 10, 10, 90, 30, 10, 50, 40, 40, 20])],
        'mqs': [{'uwy': u, 'lr': v} for u, v in zip(
            ['24/25', '25/26'], [60, 80])],
    },
}


class Command(BaseCommand):
    help = 'Load the locked 11-year reinsurance treaty-performance dataset (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Overwrite the base figures even if the dataset exists.')

    def handle(self, *args, **opts):
        obj = ReinsuranceHistory.current()
        if obj and not opts['force']:
            self.stdout.write(self.style.WARNING(
                f'Already present (v{obj.version}, locked={obj.locked}) — left unchanged. '
                f'Use --force to re-seed the base figures.'))
            return
        if obj is None:
            obj = ReinsuranceHistory(key=ReinsuranceHistory.SINGLETON_KEY)
        obj.data = DATASET
        obj.locked = True
        obj.source_note = 'Seeded from broker treaty-stats workbooks, as at 31 Mar 2026.'
        obj.save()
        self.stdout.write(self.style.SUCCESS(
            f'Reinsurance 11-year history seeded (v{obj.version}, locked=True).'))
