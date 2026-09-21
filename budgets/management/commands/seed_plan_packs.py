"""
seed_plan_packs — put the three AD Insurtech 5-Year plan packs in the library.

Spec §7 (AD_Insurtech_StrategicPlanLibrary_Spec, Finance 2026-08-04): three packs
seeded at launch — Base Case, Conservative, Aggressive — all DRAFT.

Deliberately NO financial figures are written here. `base_figures` stays empty on
a draft and the Library page computes the KPIs from lib/fiveYearModel.ts, the very
same model the Cockpit runs. Hardcoding numbers in a seed script is how a library
starts quietly disagreeing with the cockpit it is meant to archive. Figures are
frozen into the pack only when someone approves it.

The lever overrides and the assumption register below are the plan's *inputs* —
those do belong here, because they are what defines each scenario.

Idempotent: re-running updates the assumption register and leaves status alone.
"""
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date

from budgets.models import PlanPack

# Key assumption register (spec §4.5). Values are the workbook's own inputs, and
# they match the taper/glide arrays in frontend/src/lib/fiveYearModel.ts.
ASSUMPTIONS = [
    {'label': 'GI GWP growth',            'segment': 'GI',      'fy26': '20%',   'fy27': '18%',   'fy28': '15%',   'fy29': '13%',   'fy30': '12%',   'source': 'Assumptions tab'},
    {'label': 'Gross loss ratio',         'segment': 'GI',      'fy26': '54%',   'fy27': '52%',   'fy28': '51%',   'fy29': '50%',   'fy30': '49%',   'source': 'Assumptions tab'},
    {'label': 'RI cession rate',          'segment': 'RI Co',   'fy26': '59.0%', 'fy27': '59.0%', 'fy28': '58.5%', 'fy29': '58.0%', 'fy30': '57.5%', 'source': 'Treaty schedule'},
    {'label': 'Health members / month',   'segment': 'Health',  'fy26': '300',   'fy27': '300',   'fy28': '300',   'fy29': '300',   'fy30': '300',   'source': 'Assumptions tab'},
    {'label': 'Fixed OpEx — FY26 base (P Mn)', 'segment': 'Group', 'fy26': '45.7', 'fy27': '—', 'fy28': '—', 'fy29': '—', 'fy30': '—', 'source': 'Assumptions tab — later years per the workbook build-up'},
    {'label': 'FY2025 base year',         'segment': 'Group',   'fy26': 'locked','fy27': '—',     'fy28': '—',     'fy29': '—',     'fy30': '—',     'source': 'Forvis Mazars signed audit 2026'},
    {'label': 'Investor capital (USD)',   'segment': 'Group',   'fy26': '5.0m',  'fy27': '—',     'fy28': '—',     'fy29': '—',     'fy30': '—',     'source': 'Term sheet — 2 tranches'},
    {'label': 'USD / BWP rate',           'segment': 'Group',   'fy26': '14.00', 'fy27': '14.00', 'fy28': '14.00', 'fy29': '14.00', 'fy30': '14.00', 'source': 'Planning rate (flat by policy)'},
    {'label': 'EV revenue multiple',      'segment': 'Group',   'fy26': '—',     'fy27': '—',     'fy28': '—',     'fy29': '—',     'fy30': '3.5x',  'source': 'Comparables tab (mid)'},
    {'label': 'Life & Funeral launch',    'segment': 'Life',    'fy26': 'FY26',  'fy27': 'scale', 'fy28': 'scale', 'fy29': 'scale', 'fy30': 'scale', 'source': 'Product roadmap'},
    {'label': 'NeoBank launch',           'segment': 'NeoBank', 'fy26': '—',     'fy27': 'FY27',  'fy28': 'scale', 'fy29': 'scale', 'fy30': 'scale', 'source': 'Product roadmap'},
    {'label': 'South Africa expansion',   'segment': 'SA',      'fy26': '—',     'fy27': 'entry', 'fy28': 'scale', 'fy29': 'scale', 'fy30': 'scale', 'source': 'Product roadmap'},
]

PACKS = [
    {
        'label': 'FY2026–FY2030 · Base Case',
        'scenario_slug': 'base',
        'narrative': (
            'The consolidated AD Insurtech plan exactly as the workbook builds it, on '
            'the FY2025 audited base. No lever moved. This is the pack every other '
            'number in the library is measured against.'
        ),
        'levers': ('None — workbook base values verbatim (GI growth 20% · loss ratio 54% · '
                   'cession 59% · Health 300/mo · Fixed OpEx P45.7m · multiple 3.5x).'),
    },
    {
        'label': 'FY2026–FY2030 · Conservative',
        'scenario_slug': 'conservative',
        'narrative': (
            'The downside we would still fund: GI growth slows to 12% in FY26, the '
            'gross loss ratio sits at 60%, more risk is ceded away at 62%, Health signs '
            '180 members a month and fixed costs run at P49.0m. Valued at 2.25x revenue.'
        ),
        'levers': ('GI growth FY26 12% · Gross loss ratio 60% · Cession 62% · '
                   'Health members 180/mo · Fixed OpEx P49.0m · Revenue multiple 2.25x.'),
    },
    {
        'label': 'FY2026–FY2030 · Aggressive',
        'scenario_slug': 'aggressive',
        'narrative': (
            'The upside case for the investor conversation: GI growth 28% in FY26, the '
            'loss ratio pulled to 49%, less ceded away at 55%, Health at 600 members a '
            'month and fixed costs held to P43.0m. Valued at 5.0x revenue.'
        ),
        'levers': ('GI growth FY26 28% · Gross loss ratio 49% · Cession 55% · '
                   'Health members 600/mo · Fixed OpEx P43.0m · Revenue multiple 5.0x.'),
    },
]


class Command(BaseCommand):
    help = 'Seed the three AD Insurtech 5-Year strategic plan packs (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--prepared-by', default='CFO Office')
        parser.add_argument('--prepared-date', default='2026-08-03')

    def handle(self, *args, **opts):
        made = updated = 0
        for spec in PACKS:
            # The register is the workbook's inputs, one value per year. The
            # scenario's lever overrides are a sentence, not a column of years —
            # they live in the pack narrative, not in a table cell (a long string
            # here squeezed every year column off the screen).
            assumptions = [dict(a) for a in ASSUMPTIONS]
            pack, created = PlanPack.objects.get_or_create(
                entity='AD_INSURTECH',
                label=spec['label'],
                scenario_slug=spec['scenario_slug'],
                defaults={
                    'status': PlanPack.Status.DRAFT,
                    'prepared_by': opts['prepared_by'],
                    'prepared_date': parse_date(opts['prepared_date']),
                    'department': 'CFO Office',
                    'narrative': spec['narrative'],
                    'assumptions': assumptions,
                },
            )
            if created:
                made += 1
                self.stdout.write(self.style.SUCCESS(f'  + {pack.label}'))
            else:
                # Refresh the inputs, never the status or the approved figures.
                pack.assumptions = assumptions
                pack.narrative = spec['narrative']
                pack.save(update_fields=['assumptions', 'narrative', 'updated_at'])
                updated += 1
                self.stdout.write(f'  · {pack.label} (assumptions refreshed)')
        self.stdout.write(self.style.SUCCESS(f'created={made} refreshed={updated}'))
