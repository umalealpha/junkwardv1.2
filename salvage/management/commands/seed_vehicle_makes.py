"""Fill the salvage Brand / Model dropdowns from Graphite's own vehicle book.

Bharath, 17-Sep-2026: "We must have a drop down of all make and models. We have
this in graphite and the claims tracker. we can pull it from there."

Graphite IS the right source — it carries the make and model of every vehicle
Alpha Direct has ever insured, which is exactly the population that ends up in
the yard. The in-repo Motor Liquidators SQLite (32 makes / 79 models, loaded by
`import_motor_liquidators`) only ever held what the yard had already seen.

Read-only throughout: the query goes through `integrations.graphite_ro`, which
refuses any statement that is not a SELECT and refuses to connect to anything
but the read replica. Nothing is written back to Graphite.

Rows are created, never overwritten — a make already in Omni keeps its own
spelling and its `is_active` flag. Re-running is safe and adds only what is new.

    python manage.py seed_vehicle_makes --dry-run
    python manage.py seed_vehicle_makes
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from salvage.models import VehicleBrand, VehicleModel

#: Graphite's `vehicle` table is free text typed by underwriters over years, so
#: it holds blanks, placeholders and single letters alongside real makes.
_JUNK = {
    '', '-', '--', 'n/a', 'na', 'nil', 'none', 'unknown', 'other', 'test',
    'tbc', 'tba', 'xxx', 'x', '.', '0', '00', '000',
}

#: Spellings that must land on a make the yard ALREADY has. The legacy Motor
#: Liquidators import seeded all 32 makes in Title Case — Volkswagen,
#: Mercedes-Benz, Land Rover — so Graphite's 'VW', 'MERCEDES BENZ' and
#: 'LANDROVER' would otherwise arrive as SECOND brands sitting next to them,
#: which is the three-spellings dropdown this command exists to prevent.
_ALIASES = {
    'vw':            'Volkswagen',
    'volkswagon':    'Volkswagen',
    'mercedes benz': 'Mercedes-Benz',
    'mercedes':      'Mercedes-Benz',
    'merc':          'Mercedes-Benz',
    'benz':          'Mercedes-Benz',
    'landrover':     'Land Rover',
    'land-rover':    'Land Rover',
}
# Deliberately NOT in here: 'range rover'. clean() runs over models as well as
# makes, and Range Rover is a real MODEL — aliasing it would file it as
# "Land Rover Land Rover".

#: Makes that are initials, not words. Title-casing turns GWM into "Gwm" and
#: BMW into "Bmw", which reads wrong and — worse — leaves the dropdown with
#: both spellings once one underwriter types it either way. Everything that
#: is a real word (Toyota, Nissan) is not in here and gets title-cased.
_ACRONYMS = {
    m.lower(): m for m in (
        'BMW', 'GWM', 'MG', 'JAC', 'BAIC', 'BAW', 'FAW', 'DFSK', 'BYD',
        'GAC', 'JMC', 'UD', 'MAN', 'DAF', 'GMC', 'RAM', 'SEAT', 'DS',
    )
}

MAX_BRAND_LEN = 80      # VehicleBrand.name
MAX_MODEL_LEN = 120     # VehicleModel.name

#: A make must sit on at least this many vehicles to reach the dropdown, and a
#: model on at least this many within its make. Graphite's make/model is typed
#: by hand on every policy, so without a floor a single fat-fingered "Toyoat"
#: becomes a permanent dropdown entry and the yard staff end up choosing
#: between three spellings of the same car.
MIN_MAKE_VEHICLES  = 3
MIN_MODEL_VEHICLES = 2


def clean(value):
    """Trim and normalise one free-text make/model, or None if it is junk.

    Case-folding is what makes 'TOYOTA', 'toyota' and 'Toyota' land on ONE
    dropdown row instead of three. Names already in mixed case (iX3, e-Golf)
    are left exactly as typed — there is no safe way to re-case those.
    """
    if value is None:
        return None
    text = ' '.join(str(value).split())
    if text.lower() in _JUNK or len(text) < 2:
        return None
    if text.lower() in _ALIASES:
        return _ALIASES[text.lower()]
    if text.lower() in _ACRONYMS:
        return _ACRONYMS[text.lower()]
    if text.isupper() or text.islower():
        text = text.title()
    return text


def tally(rows, min_make=MIN_MAKE_VEHICLES, min_model=MIN_MODEL_VEHICLES):
    """Fold ``[{make, model, n}, …]`` into ``{make: {model, …}}``.

    Counting happens AFTER cleaning, so 'TOYOTA' (900) and 'toyota' (40) add
    up to one Toyota on 940 vehicles rather than two makes that might each
    miss the floor on their own.
    """
    counts = {}          # make -> total vehicles
    models = {}          # make -> {model: vehicles}
    for r in rows:
        make = clean(r.get('make'))
        if not make or len(make) > MAX_BRAND_LEN:
            continue
        n = int(r.get('n') or 1)
        counts[make] = counts.get(make, 0) + n
        model = clean(r.get('model'))
        if model and len(model) <= MAX_MODEL_LEN:
            per = models.setdefault(make, {})
            per[model] = per.get(model, 0) + n

    return {
        make: {m for m, mn in models.get(make, {}).items() if mn >= min_model}
        for make, total in counts.items()
        if total >= min_make
    }


class Command(BaseCommand):
    help = "Seed salvage VehicleBrand/VehicleModel from Graphite's vehicle table."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be added and write nothing.',
        )
        parser.add_argument(
            '--limit', type=int, default=20000,
            help='Cap on distinct make/model pairs read from Graphite.',
        )
        parser.add_argument(
            '--min-count', type=int, default=MIN_MAKE_VEHICLES,
            help=(
                'A make must appear on at least this many vehicles to reach '
                f'the dropdown (default {MIN_MAKE_VEHICLES}). Free text means '
                'one mistyped policy would otherwise become a permanent '
                'dropdown entry.'
            ),
        )

    def handle(self, *args, **opts):
        from integrations import graphite_ro

        # COUNT(DISTINCT policy_id), never COUNT(*): Graphite clones a vehicle
        # row per action_id on EVERY renewal (aware/engine.py:93 — "History is
        # duplicated per renewal"). With COUNT(*) one mistyped policy renewed
        # three times counts as three vehicles and walks straight through the
        # floor below, which is the exact typo the floor exists to stop.
        #
        # ORDER BY n DESC, never by name: graphite_ro.query() ends in
        # fetchmany(limit) and truncates in SILENCE. Ordered by make, a
        # truncated read quietly drops the tail of the alphabet — Toyota and
        # Volkswagen — and still prints a cheerful "Added N makes".
        try:
            rows = graphite_ro.query(
                'SELECT make, model, COUNT(DISTINCT policy_id) AS n FROM vehicle '
                'WHERE deleted_at IS NULL AND make IS NOT NULL '
                'GROUP BY make, model ORDER BY n DESC',
                limit=opts['limit'] + 1,
            )
        except Exception as exc:                       # noqa: BLE001
            raise CommandError(f'Could not read Graphite: {exc}') from exc

        if not rows:
            raise CommandError(
                'Graphite returned no vehicles. Refusing to report success on '
                'an empty read — check the replica credentials.'
            )
        # Asked for one more than the cap, so a full read means it truncated.
        if len(rows) > opts['limit']:
            raise CommandError(
                f'Graphite read hit the {opts["limit"]} cap, so makes were '
                'silently dropped. Raise --limit and run again.'
            )

        pairs = tally(rows, min_make=opts['min_count'])
        if not pairs:
            raise CommandError(
                f'Nothing survived the --min-count {opts["min_count"]} filter. '
                'Lower it or check the data.'
            )

        new_brands, new_models = [], []
        existing_brands = {b.name for b in VehicleBrand.objects.all()}
        for make in sorted(pairs):
            if make not in existing_brands:
                new_brands.append(make)

        if opts['dry_run']:
            have = set(
                VehicleModel.objects.values_list('brand__name', 'name')
            )
            for make, models in pairs.items():
                for m in models:
                    if (make, m) not in have:
                        new_models.append(f'{make} {m}')
            self.stdout.write(
                f'Graphite gave {len(rows)} distinct pairs -> '
                f'{len(pairs)} makes.\n'
                f'Would ADD {len(new_brands)} makes and {len(new_models)} models.\n'
                f'Sample makes: {", ".join(sorted(new_brands)[:15]) or "(none)"}'
            )
            return

        # The pre-scan above already listed what is new, and the loop below
        # appends again — leaving it would report DOUBLE the makes actually
        # added, and a wrong number told to the operator is its own bug.
        new_brands = []

        with transaction.atomic():
            for make in sorted(pairs):
                brand, made = VehicleBrand.objects.get_or_create(
                    name=make, defaults={'is_active': True},
                )
                if made:
                    new_brands.append(make)
                for m in sorted(pairs[make]):
                    _, made_m = VehicleModel.objects.get_or_create(
                        brand=brand, name=m, defaults={'is_active': True},
                    )
                    if made_m:
                        new_models.append(f'{make} {m}')

        self.stdout.write(self.style.SUCCESS(
            f'Added {len(new_brands)} makes and {len(new_models)} models. '
            f'Dropdown now holds {VehicleBrand.objects.count()} makes and '
            f'{VehicleModel.objects.count()} models.'
        ))
