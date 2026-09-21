"""
fix_far_defects — CFO directive 2026-05-20 (Internal Audit memo
OMNI-FAR-FIX-001).

Idempotent one-shot for four FAR defects:

  DR-001 CRITICAL — 269 assets misclassified into ADIC-210002 Motor
                     Vehicles. Re-categorise by name keywords into
                     ADIC-220002/220003/220004/220005.
  DR-002 CRITICAL — 11 duplicate vehicle records. Flag numeric-tag
                     versions as status='migrated_duplicate'. No hard
                     delete — CFO confirms canonicals first.
  DR-003 HIGH      — Acquisition-date cut-off filter for the FAR report.
                     Code fix lives in reporting/views.py AssetRegisterView.
                     This command flags affected records for review only.
  DR-004 MEDIUM    — Tag 384 has purchase_date 2507-07-24 — fix to
                     2025-07-24.

Usage:
    python manage.py fix_far_defects            # dry-run
    python manage.py fix_far_defects --commit   # actually write
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


# ---------------------------------------------------------------------------
# DR-002: numeric tags that duplicate canonical ADI-FAR-* records.
# ---------------------------------------------------------------------------
DUPLICATE_NUMERIC_TAGS = [
    '90',                  # → ADI-FAR-0001 Toyota Vitz B896BKZ
    '73',                  # → ADI-FAR-0002 Suzuki Swift B505BNE
    '74',                  # → ADI-FAR-0003 Isuzu D-Max Bharath
    '75',                  # → ADI-FAR-0004 Isuzu D-Max Butale
    '76',                  # → ADI-FAR-0005 Isuzu D-Max Paul Beka
    '344',                 # → ADI-FAR-0006 Lexus GX550 CEO
    '345',                 # → ADI-FAR-0007 Nissan Civilian Bus
    '349',                 # → ADI-FAR-0008 Fortuner COO
    '356',                 # → ADI-FAR-0009 Toyota Vitz B150BWX
    '357',                 # → ADI-FAR-0010 Toyota Vitz B154BWX
    '389',                 # → ADI-FA26-0011 Fortuner Bharath B944BTB
]


# ---------------------------------------------------------------------------
# Names matching any of these belong in Motor Vehicles and must NOT be
# re-categorised. Vehicle quarantine (DR-002) handles their duplicate
# status separately.
# ---------------------------------------------------------------------------
VEHICLE_KEEP_KEYWORDS = [
    'toyota', 'suzuki', 'isuzu', 'nissan', 'lexus', 'bmw', 'fortuner',
    'd-max', 'd max', 'civilian', 'swift', 'vitz', 'vits', 'hilux',
    'corolla', 'fortuner', 'land cruiser', 'landcruiser',
]


# ---------------------------------------------------------------------------
# DR-001: keyword → target category code. Priority order matters — first
#         match wins. Lowercased name match against the asset's `name`.
# ---------------------------------------------------------------------------
KEYWORD_MAP = [
    # IT — laptops, servers, monitors, network gear, tablets, peripherals
    ('ADIC-220003', [
        'computer', 'laptop', 'server', 'monitor', 'keyboard', 'mouse',
        'tablet', 'ipad', 'iphone', 'printer', 'scanner', 'router',
        'switch', 'firewall', 'access point', 'ups',
        # brands & models
        'hp ', 'hp250', 'hp 250', 'hp probook', 'probook', 'elitebook',
        'lenovo', 'ideapad', 'thinkpad', 'thinkbook', 'thinkcentre',
        'thinkvision', 'dell', 'vostro', 'inspiron', 'asus', 'vivobook',
        'macbook', 'mac mini', 'apple watch', 'samsung galaxy',
        'galaxy tab', 'huawei', 'mediapad', 'itel', 'samasung',
        # peripherals & accessories
        'headset', 'head set', 'dock', 'usb', 'ssd', 'hdd', 'ddr4', 'ram',
        'lexar', 'flybox', 'starlink', 'mecer', 'epson', 'workforce',
        'samsung tab', 'samasung galaxy', 'samasung tab', 'tab cover',
        'tab covers', 'mount kit', 'canoslide', 'canoscan',
        # software & licenses
        'windows 11', 'win 11', 'win11', 'license',
        # IT-coded tags
        'it-lap', 'it-mon', 'ad-cam',
    ]),
    # Office equipment — appliances, machines, branding, comms
    ('ADIC-220002', [
        'fridge', 'microwave', 'kettle', 'dispenser', 'projector',
        'whiteboard', 'phone', 'calculator', 'photocopier', 'shredder',
        'safe', 'office equipment', 'coffee', 'coffe', 'popcorn',
        'gas stove', 'stove', 'air cooler', 'vacuum', 'hydrovac',
        'magnetic board', 'engraving',
    ]),
    # Electrical & fittings — lights, fans, AC, geyser, wiring, cameras
    ('ADIC-220005', [
        'electrical', 'fitting', 'light', 'lamp', 'fan ', 'aircon',
        'ac unit', 'geyser', 'socket', 'wiring', 'cabling', 'gate motor',
        'cctv', 'camera', 'alarm', 'access control',
        'cam ', ' cam', 'wyze', 'surveillance', 'hikvision',
        'tv', 'hisense', 'skyworth', 'aluminium door', 'aluminum door',
        'door closer', 'bidet',
    ]),
    # Furniture — chairs, desks, tables, cabinets, branding/signage,
    # finishings (blinds, wallpaper, ceramic, grass, plexiglass)
    ('ADIC-220004', [
        'chair', 'desk', 'table', 'cabinet', 'shelf', 'sofa', 'couch',
        'bookshelf', 'bookcase', 'drawer', 'locker', 'partition',
        'workstation', 'pedestal',
        'bar stool', 'barstool', 'stool', 'counter', 'shelving',
        'cradle', 'mobile unit', 'roller blind', 'roller blinds',
        'blinds', 'wallpaper', 'ceramic', 'artificial grass',
        'aritificial grass', 'plexiglass', 'kiosk', 'furniture',
        'office furniture', 'branding', 'brand designing',
        'office brand', 'display stand', 'cupboard', 'roller cupboard',
        'laser cut', 'laser cutting', 'steel piece', 'greenwall',
    ]),
]
# Source category to migrate FROM. Anything currently in this category
# whose name doesn't match any keyword stays put (caller will see them in
# the dry-run summary and can refine the keyword list).
SOURCE_CATEGORY_CODE = 'ADIC-210002'

_KEEP_VEHICLE = object()  # sentinel: leave in Motor Vehicles


def _classify(name: str):
    """Return target category code, _KEEP_VEHICLE sentinel, or None."""
    if not name:
        return None
    lc = name.lower()
    for kw in VEHICLE_KEEP_KEYWORDS:
        if kw in lc:
            return _KEEP_VEHICLE
    for target_code, keywords in KEYWORD_MAP:
        for kw in keywords:
            if kw in lc:
                return target_code
    return None


class Command(BaseCommand):
    help = 'Apply FAR defects remediation (CFO memo 2026-05-20).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit', action='store_true',
            help='Actually write to the database. Default is dry-run.',
        )

    def handle(self, *args, **opts):
        from assets.models import Asset, AssetCategory
        commit = opts['commit']
        tag = 'COMMIT' if commit else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[{tag}] FAR defects remediation — OMNI-FAR-FIX-001'
        ))

        # Resolve target categories up-front so the script fails fast if any
        # are missing rather than mid-pass.
        target_codes = {c for c, _ in KEYWORD_MAP} | {SOURCE_CATEGORY_CODE}
        cats = {c.code: c for c in AssetCategory.objects.filter(code__in=target_codes)}
        missing = target_codes - set(cats)
        if missing:
            raise CommandError(
                f'Missing AssetCategory rows: {sorted(missing)}. '
                f'Seed them before re-running.'
            )

        # ── DR-002: flag duplicates ────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\nDR-002 — flag 11 duplicate vehicle records'
        ))
        dup_qs = Asset.objects.filter(tag_number__in=DUPLICATE_NUMERIC_TAGS)
        for a in dup_qs:
            already = a.status == Asset.Status.MIGRATED_DUPLICATE
            self.stdout.write(
                f'  tag={a.tag_number:<6} name={a.name[:48]:<48} '
                f'current_status={a.status}{"  [already flagged]" if already else ""}'
            )
        if commit:
            n = dup_qs.exclude(status=Asset.Status.MIGRATED_DUPLICATE).update(
                status=Asset.Status.MIGRATED_DUPLICATE,
            )
            self.stdout.write(self.style.SUCCESS(f'  flagged {n} row(s)'))
        else:
            self.stdout.write(f'  [dry-run] would flag '
                              f'{dup_qs.exclude(status=Asset.Status.MIGRATED_DUPLICATE).count()} row(s)')

        # ── DR-004: fix tag 384 purchase_date ──────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\nDR-004 — tag 384 purchase_date 2507-07-24 → 2025-07-24'
        ))
        target = Asset.objects.filter(tag_number='384').first()
        if not target:
            self.stdout.write('  tag 384 not found — skipping')
        else:
            self.stdout.write(
                f'  before: purchase_date={target.purchase_date}'
            )
            if target.purchase_date and target.purchase_date.year == 2507:
                if commit:
                    target.purchase_date = date(2025, 7, 24)
                    target.save(update_fields=['purchase_date', 'updated_at'])
                    self.stdout.write(self.style.SUCCESS(
                        '  after:  purchase_date=2025-07-24'
                    ))
                else:
                    self.stdout.write('  [dry-run] would set to 2025-07-24')
            else:
                self.stdout.write('  already correct — no change')

        # ── DR-001: re-categorise misclassified assets ─────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\nDR-001 — re-categorise 269 misclassified assets (keyword match)'
        ))
        src = cats[SOURCE_CATEGORY_CODE]
        miscls_qs = Asset.objects.filter(category=src)
        moved_by_target: dict[str, int] = {}
        no_match: list[tuple[str, str]] = []
        kept_vehicles = 0
        gross_moved = Decimal('0.00')
        for a in miscls_qs.iterator(chunk_size=200):
            target = _classify(a.name)
            if target is _KEEP_VEHICLE:
                kept_vehicles += 1
                continue
            if not target:
                no_match.append((a.tag_number, a.name))
                continue
            moved_by_target[target] = moved_by_target.get(target, 0) + 1
            gross_moved += (a.cost or Decimal('0'))
            if commit:
                a.category = cats[target]
                a.save(update_fields=['category', 'updated_at'])

        for code, n in sorted(moved_by_target.items()):
            self.stdout.write(f'  -> {code}: {n} rows')
        self.stdout.write(f'  total moved: {sum(moved_by_target.values())}')
        self.stdout.write(f'  gross BV moved: BWP {gross_moved:,.2f}')
        self.stdout.write(f'  kept as Motor Vehicles (correct): {kept_vehicles}')
        self.stdout.write(f'  no keyword match (still in {SOURCE_CATEGORY_CODE}): '
                          f'{len(no_match)}')
        if no_match and not commit:
            self.stdout.write('  first 20 unmatched (refine KEYWORD_MAP if needed):')
            for tag, name in no_match[:20]:
                self.stdout.write(f'    tag={tag:<6} name={name[:70]}')

        self.stdout.write(self.style.SUCCESS(
            f'\n[{tag}] done. Re-run with --commit to apply.'
            if not commit else f'\n[{tag}] done.'
        ))
