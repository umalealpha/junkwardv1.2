"""
seed_burs_2026_2027 — Botswana PAYE tax brackets from 1 July 2026.

CFO directive 2026-08-17. The **Income Tax Act, 2026** (assented 29 June 2026,
in force 1 July 2026) added a SIXTH band to the individual schedule:

    27.5 % on annual taxable income above BWP 400,000

The five lower bands are UNCHANGED — the only structural change is that the old
open-ended "156,000 +" band is now capped at 400,000 and the new band sits on
top of it.

Sources (all read 2026-08-17):
  * KPMG TaxNewsFlash, 9 Jul 2026 — "the introduction of a top marginal tax rate
    of 27.5% for those earning above BWP P400,000 per annum (BWP P33,333
    monthly)", effective 1 July 2026.
  * RSM Botswana — "Botswana tax laws change significantly effective 1 July 2026".
  * CRS News Flash, 17 Jul 2026 — Botswana Tax Changes 2026-2027.
  (PwC Worldwide Tax Summaries still shows a 25 % top rate — that page was last
   reviewed 9 Jan 2026 and is stale for this change. Do not use it as authority.)

Schedule (annual taxable income, BWP):

  Resident individual:
    0 – 48,000             0 %
    48,001 – 84,000        5 % over 48,000              (base 0)
    84,001 – 120,000       12.5 % over 84,000           (base 1,800)
    120,001 – 156,000      18.75 % over 120,000         (base 6,300)
    156,001 – 400,000      25 % over 156,000            (base 13,050)
    400,001 +              27.5 % over 400,000          (base 74,050)   ← NEW

  Non-resident individual (no tax-free band) — seeded INACTIVE, see below:
    0 – 84,000             5 % over 0                   (base 0)
    84,001 – 120,000       12.5 % over 84,000           (base 4,200)
    120,001 – 156,000      18.75 % over 120,000         (base 8,700)
    156,001 – 400,000      25 % over 156,000            (base 15,450)
    400,001 +              27.5 % over 400,000          (base 76,450)   ← NEW

Base amounts are cumulative tax at the band floor, derived from the band below:
  resident     13,050 + 25 % × (400,000 − 156,000) = 13,050 + 61,000 = 74,050
  non-resident 15,450 + 25 % × (400,000 − 156,000) = 15,450 + 61,000 = 76,450

⚠️  WHY NON-RESIDENT IS SEEDED **INACTIVE** — do not "fix" this.
    TaxBracket has no residency field and `payroll.paye.active_brackets_from_db()`
    orders only by lower_bound, so two active overlapping sets make band
    selection order-ambiguous. On 2026-06-10 both sets were active in prod and
    the engine applied NON-RESIDENT rates to most bands (P150/month PAYE on a
    P3,000/month salary that should have been zero). The fix was to deactivate
    the non-resident rows. This command preserves that: resident bands active,
    non-resident bands stored but inactive. All ADIC employees are residents.
    If a genuine non-resident is ever hired their PAYE needs a manual override
    until residency-aware brackets are coded.

Run:
    python manage.py seed_burs_2026_2027 --dry-run
    python manage.py seed_burs_2026_2027
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from payroll.models import TaxBracket


EFFECTIVE_FROM = date(2026, 7, 1)
TOP_BAND_FLOOR = Decimal('400000')

RESIDENT_BRACKETS = [
    # (lower, upper, base, rate_pct)
    (Decimal('0'),       Decimal('48000'),  Decimal('0'),      Decimal('0')),
    (Decimal('48000'),   Decimal('84000'),  Decimal('0'),      Decimal('5')),
    (Decimal('84000'),   Decimal('120000'), Decimal('1800'),   Decimal('12.5')),
    (Decimal('120000'),  Decimal('156000'), Decimal('6300'),   Decimal('18.75')),
    (Decimal('156000'),  TOP_BAND_FLOOR,    Decimal('13050'),  Decimal('25')),
    (TOP_BAND_FLOOR,     None,              Decimal('74050'),  Decimal('27.5')),
]

NON_RESIDENT_BRACKETS = [
    (Decimal('0'),       Decimal('84000'),  Decimal('0'),      Decimal('5')),
    (Decimal('84000'),   Decimal('120000'), Decimal('4200'),   Decimal('12.5')),
    (Decimal('120000'),  Decimal('156000'), Decimal('8700'),   Decimal('18.75')),
    (Decimal('156000'),  TOP_BAND_FLOOR,    Decimal('15450'),  Decimal('25')),
    (TOP_BAND_FLOOR,     None,              Decimal('76450'),  Decimal('27.5')),
]

RESIDENT_LABEL     = 'BURS Resident Individual — FY2026-27 (eff 1 Jul 2026)'
NON_RESIDENT_LABEL = 'BURS Non-Resident Individual — FY2026-27 (eff 1 Jul 2026)'


class Command(BaseCommand):
    help = ('Seed the PAYE schedule effective 1 Jul 2026 — adds the new 27.5 % '
            'band above BWP 400,000. Resident bands become the only ACTIVE set; '
            'non-resident bands are stored inactive (overlap trap, see docstring).')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        dry = opts['dry_run']

        before = list(
            TaxBracket.objects.filter(is_active=True).order_by('lower_bound')
            .values_list('lower_bound', 'upper_bound', 'rate_pct')
        )
        self.stdout.write('Active bands BEFORE:')
        for lo, hi, rate in before:
            self.stdout.write(f'    {lo}–{hi if hi is not None else "∞"} @ {rate}%')

        with transaction.atomic():
            # Retire every currently-active band, whatever its label or date —
            # the new resident set replaces the whole schedule. Overlapping
            # active sets are the 2026-06-10 bug; never leave two sets active.
            retired = (TaxBracket.objects
                       .filter(is_active=True)
                       .exclude(effective_from=EFFECTIVE_FROM, name=RESIDENT_LABEL)
                       .update(is_active=False))
            self.stdout.write(self.style.WARNING(
                f'Retired {retired} previously-active band(s).'))

            n_res = self._seed(RESIDENT_BRACKETS, RESIDENT_LABEL, active=True)
            n_non = self._seed(NON_RESIDENT_BRACKETS, NON_RESIDENT_LABEL, active=False)

            active_after = list(
                TaxBracket.objects.filter(is_active=True).order_by('lower_bound')
                .values_list('lower_bound', 'upper_bound', 'base_amount', 'rate_pct')
            )
            # Fail loudly rather than leave prod with a half-applied schedule.
            self._assert_schedule_sane(active_after)

            if dry:
                transaction.set_rollback(True)

        self.stdout.write('')
        self.stdout.write('Active bands AFTER:')
        for lo, hi, base, rate in active_after:
            self.stdout.write(
                f'    {lo}–{hi if hi is not None else "∞"} @ {rate}% (base {base})')
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"DRY RUN  " if dry else ""}Seeded {n_res} resident (active) + '
            f'{n_non} non-resident (inactive) band(s), effective 1 Jul 2026.'))
        if dry:
            self.stdout.write(self.style.WARNING('No DB changes (--dry-run).'))

    def _seed(self, bands, label, *, active: bool) -> int:
        for lo, hi, base, rate in bands:
            _, created = TaxBracket.objects.update_or_create(
                effective_from=EFFECTIVE_FROM,
                lower_bound=lo,
                name=label,
                defaults={
                    'upper_bound': hi,
                    'base_amount': base,
                    'rate_pct':    rate,
                    'is_active':   active,
                },
            )
            cap = hi if hi is not None else '∞'
            self.stdout.write(
                f'  {"+" if created else "~"} [{"ACTIVE  " if active else "inactive"}] '
                f'{lo}–{cap} @ {rate}% (base {base})')
        return len(bands)

    def _assert_schedule_sane(self, active_bands):
        """One active, contiguous, non-overlapping schedule ending open-ended."""
        if len(active_bands) != len(RESIDENT_BRACKETS):
            raise RuntimeError(
                f'Expected {len(RESIDENT_BRACKETS)} active bands, found '
                f'{len(active_bands)} — refusing to leave prod ambiguous.')
        prev_upper = Decimal('0')
        for i, (lo, hi, _base, _rate) in enumerate(active_bands):
            if Decimal(lo) != prev_upper:
                raise RuntimeError(
                    f'Band {i} starts at {lo} but the previous band ended at '
                    f'{prev_upper} — gap or overlap in the active schedule.')
            if hi is None:
                if i != len(active_bands) - 1:
                    raise RuntimeError(
                        f'Band {i} is open-ended but is not the last band.')
            else:
                prev_upper = Decimal(hi)
        if active_bands[-1][1] is not None:
            raise RuntimeError('The top active band must be open-ended.')
