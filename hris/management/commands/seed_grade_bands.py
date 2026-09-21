"""
Seed 8 Alpha Direct pay grades with midpoints derived from the live
May-2026 Odoo payroll register (forwarded by Unami Butale 2026-06-03).

CoS §7.1 splits annual leave entitlement across 6 grade levels (18 → 25
days). We mirror that taxonomy as 8 Grade rows in hris.Grade, with the
midpoint set to the median observed Basic Salary in the matching cohort
for May-2026 (76 employees with non-zero Basic across 14 departments).

PROPOSED midpoints (CFO directive 2026-06-03 — provisional, awaiting
Unami sign-off):

  code  level  vac-link        name                    midpoint  spread
  D1      1    VAC_DRIVER      Driver / Intern / Attaché   2,000   30
  J2      2    VAC_JR_ASSOC    Junior Associate             4,000   35
  A3      3    VAC_ASSOC       Associate                    5,500   35
  S4      4    VAC_SR_ASSOC    Senior Associate             8,000   40
  AM5     5    VAC_ASST_MGR    Assistant Manager           12,000   40
  M6      6    VAC_MGR         Manager                     22,000   45
  SM7     7    VAC_MGR         Senior Manager              49,950   50
  EX8     8    VAC_MGR         C-Suite / Exco              63,750   60

These figures match the May-2026 register medians per department cohort
(Drivers/Business Dev → D1, Compliance/Underwriting/Finance/Claims/IT →
J2-A3-S4 bands, Sales Mktg → M6 22k, Senior Management → SM7 49,950,
C-Suite → EX8 63,750).

Idempotent — uses Grade.code as natural key. Re-run any time.
"""
from decimal import Decimal as D
from django.core.management.base import BaseCommand

from hris.models import Grade


SEED = [
    # code, level, name, midpoint_bwp, spread_pct
    ('D1',  1, 'Driver / Intern / Industry Attaché',  2_000, 30),
    ('J2',  2, 'Junior Associate',                    4_000, 35),
    ('A3',  3, 'Associate',                           5_500, 35),
    ('S4',  4, 'Senior Associate',                    8_000, 40),
    ('AM5', 5, 'Assistant Manager',                  12_000, 40),
    ('M6',  6, 'Manager',                            22_000, 45),
    ('SM7', 7, 'Senior Manager',                     49_950, 50),
    ('EX8', 8, 'C-Suite / Exco',                     63_750, 60),
]


class Command(BaseCommand):
    help = 'Seed 8 pay grades with midpoints derived from May-2026 Odoo payroll medians.'

    def handle(self, *args, **opts):
        created = updated = 0
        for code, level, name, mid, spread in SEED:
            obj, made = Grade.objects.update_or_create(
                code=code,
                defaults={
                    'name':      name,
                    'level':     level,
                    'midpoint':  D(str(mid)),
                    'spread':    spread,
                    'is_active': True,
                },
            )
            band_min = obj.band_min
            band_max = obj.band_max
            tag = 'CREATED' if made else 'updated'
            self.stdout.write(
                f'  {tag}  {code:<4} L{level}  mid={mid:>7,}  spread={spread:>2}%  '
                f'band=[{band_min:>7,.0f} … {band_max:>7,.0f}]  {name}'
            )
            if made: created += 1
            else:    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {created} created, {updated} updated. '
            f'Total active grades: {Grade.objects.filter(is_active=True).count()}'
        ))
