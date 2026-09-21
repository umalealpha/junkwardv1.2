"""
import_fixed_assets_adic.py — load Alpha Direct Insurance fixed asset register
from a JSON payload produced by tools/fa_xlsx_to_json.py.

The CFO's xlsx workbooks (FY25 audited + March 2026 depreciation schedule) are
parsed locally on the Mac into a single JSON payload; this command consumes
that JSON on the server side. xlsx files never need to live on prod.

Idempotent: re-running with --commit on the same payload only inserts assets
whose `external_ref` is not yet present.

Usage:
    python manage.py import_fixed_assets_adic --json /tmp/fa_payload.json
    python manage.py import_fixed_assets_adic --json /tmp/fa_payload.json --commit
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from assets.models import Asset, AssetCategory
from core.models import Company
from ledger.models import Account
from django.utils import timezone

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')

# ADIC-specific category map. Each cost code in the xlsx maps to:
#   (label,
#    cost_account_code,
#    accum_dep_account_code,
#    dep_expense_account_code,
#    default_useful_life_months,
#    method)
#
# All accounts must already exist in the chart of accounts; if any are missing
# the command will raise so the operator knows to seed CoA first.
ADIC_CATEGORY_MAP: dict[str, tuple] = {
    '210002': ('Motor Vehicles',          '210002', '152010', '600040',  48, 'straight_line'),
    '220001': ('GFS Software',            '220001', '1450',   '600040',  48, 'straight_line'),
    '220002': ('Office Equipment',        '220002', '152030', '600040',  48, 'straight_line'),
    '220003': ('Computers & Network',     '220003', '152020', '600040',  48, 'straight_line'),
    '220004': ('Office Furniture',        '220004', '152030', '600040',  72, 'straight_line'),
    '220005': ('Fixtures & Fittings',     '220005', '152030', '600040',  72, 'straight_line'),
    '220006': ('Renovations',             '220005', '152030', '600040',  72, 'straight_line'),
}


def _parse_date(v) -> date | None:
    if v in (None, ''):
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _dec(v) -> Decimal:
    if v in (None, ''):
        return ZERO
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'))
    except Exception:  # noqa: BLE001
        return ZERO


def _slugify_ref(name: str, idx: int) -> str:
    """Stable external_ref derived from name + row index."""
    base = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_').upper()[:60]
    return f'FAR2026-{idx:04d}-{base}'


class Command(BaseCommand):
    help = 'Import Alpha Direct Insurance fixed asset register from JSON payload.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--json', required=True,
            help='Path to JSON (or .json.gz) payload produced by parse_far.py',
        )
        parser.add_argument(
            '--commit', action='store_true',
            help='Actually write rows. Without this, dry-run reports only.',
        )
        parser.add_argument(
            '--company-code', default='ADIC',
            help='alpha-finance Company.code to attach assets to.',
        )
        parser.add_argument(
            '--user', default='admin',
            help='username of the importer-of-record.',
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        path = opts['json']
        commit = opts['commit']
        company_code = opts['company_code']
        username = opts['user']

        # Load payload (gz or plain)
        if path.endswith('.gz'):
            with gzip.open(path, 'rt') as fh:
                payload = json.load(fh)
        else:
            with open(path) as fh:
                payload = json.load(fh)

        fy25 = payload.get('fy25', [])
        mar26 = payload.get('mar26', [])
        self.stdout.write(self.style.NOTICE(
            f'Payload: FY25={len(fy25)} March2026={len(mar26)} '
            f"commit={commit} company={company_code}"
        ))

        company = Company.objects.filter(code__iexact=company_code).first()
        if not company:
            raise CommandError(f'No Company with code={company_code!r} found.')

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'User {username!r} not found.')

        # Ensure categories exist (idempotent)
        category_by_code = self._ensure_categories(commit)

        # Phase 1 — seed FY25 register (state at 30/06/2025)
        seeded = 0
        skipped = 0
        failed = 0
        errors = []
        fy25_by_key: dict[str, str] = {}   # (cat_code, name) → tag_number

        for idx, row in enumerate(fy25, start=1):
            cat_code = row.get('cat_code')
            name = row.get('name', '(unnamed)')
            key = f"{cat_code}|{name}"
            external_ref = _slugify_ref(name, idx)

            cat = category_by_code.get(cat_code)
            if cat is None:
                failed += 1
                errors.append(f"FY25 row {idx}: unknown cat_code={cat_code!r}")
                continue

            if Asset.objects.filter(external_ref=external_ref).exists():
                fy25_by_key[key] = external_ref
                skipped += 1
                continue

            tag = f'ADI-FAR-{idx:04d}'
            if Asset.objects.filter(tag_number=tag).exists():
                # collision (re-run) — bump to avoid unique constraint
                suffix = 1
                while Asset.objects.filter(tag_number=f'{tag}-{suffix}').exists():
                    suffix += 1
                tag = f'{tag}-{suffix}'

            method = (row.get('method') or '').lower()
            method = 'straight_line' if 'linear' in method or 'straight' in method else cat.default_method
            useful_life = int(row.get('useful_life_months') or cat.default_useful_life_months)
            purchase_date = _parse_date(row.get('acquisition_date')) or date(2025, 6, 30)
            last_dep = _parse_date(row.get('last_dep_date')) or date(2025, 6, 30)
            cost = _dec(row.get('cost'))
            accum = _dec(row.get('opening_accum_depr'))

            if cost <= 0:
                skipped += 1
                continue   # disposed or never capitalised — skip

            if not commit:
                seeded += 1
                fy25_by_key[key] = external_ref
                continue

            try:
                with transaction.atomic():
                    Asset.objects.create(
                        tag_number=tag,
                        external_ref=external_ref,
                        name=name[:200],
                        description='',
                        company=company,
                        category=cat,
                        cost=cost,
                        salvage_value=ZERO,
                        method=method,
                        useful_life_months=useful_life,
                        purchase_date=purchase_date,
                        in_service_date=purchase_date,
                        opening_accumulated_depreciation=accum,
                        last_depreciation_date=last_dep,
                        location='Gaborone',
                        custodian='',
                        status=Asset.Status.ACTIVE,
                        created_by=user,
                        notes=f'Migrated from FY25 audited FAR on {timezone.localdate().isoformat()}',
                    )
                seeded += 1
                fy25_by_key[key] = external_ref
            except Exception as exc:  # noqa: BLE001
                failed += 1
                errors.append(f"FY25 row {idx} {name[:50]!r}: {exc}")
                logger.exception('FY25 import row %s failed', idx)

        # Phase 2 — apply March 2026 deltas
        # Strategy:
        #   - new acquisitions in FY26: create Asset (status=active)
        #   - disposals in FY26: mark existing Asset status=disposed
        #   - YTD depreciation: bump opening_accumulated_depreciation by fy26_ytd_dep,
        #     advance last_depreciation_date to 2026-03-31
        delta_new = 0
        delta_disposed = 0
        delta_dep_bump = 0
        for idx, row in enumerate(mar26, start=1):
            cat_code = row.get('cat_code')
            name = row.get('name', '(unnamed)')
            cat = category_by_code.get(cat_code)
            if cat is None:
                continue

            if row.get('is_new_fy26'):
                fy26_ref = f'FAR2026-NEW-{idx:04d}'
                fy26_tag = f'ADI-FA26-{idx:04d}'
                if Asset.objects.filter(external_ref=fy26_ref).exists():
                    continue
                cost = _dec(row.get('additions_fy26'))
                if cost <= 0:
                    continue
                if not commit:
                    delta_new += 1
                    continue
                try:
                    with transaction.atomic():
                        Asset.objects.create(
                            tag_number=fy26_tag,
                            external_ref=fy26_ref,
                            name=name[:200],
                            company=company,
                            category=cat,
                            cost=cost,
                            method=cat.default_method,
                            useful_life_months=int(row.get('useful_life_months') or cat.default_useful_life_months),
                            purchase_date=_parse_date(row.get('acquisition_date')) or date(2025, 7, 1),
                            in_service_date=_parse_date(row.get('acquisition_date')) or date(2025, 7, 1),
                            opening_accumulated_depreciation=_dec(row.get('fy26_ytd_dep')),
                            last_depreciation_date=date(2026, 3, 31),
                            status=Asset.Status.ACTIVE,
                            created_by=user,
                            notes='FY26 acquisition (March 2026 dep schedule)',
                        )
                    delta_new += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    errors.append(f"FY26 new row {idx} {name[:50]!r}: {exc}")
                continue

            # existing asset — bump dep + maybe disposal
            key = f"{cat_code}|{name}"
            ref = fy25_by_key.get(key)
            if not ref:
                continue
            qs = Asset.objects.filter(external_ref=ref)
            if not qs.exists():
                continue

            updates: dict = {}
            if row.get('is_disposed_fy26'):
                updates['status'] = Asset.Status.DISPOSED
                delta_disposed += 1

            ytd_dep = _dec(row.get('fy26_ytd_dep'))
            if ytd_dep > ZERO:
                a = qs.first()
                updates['opening_accumulated_depreciation'] = (a.opening_accumulated_depreciation + ytd_dep)
                updates['last_depreciation_date'] = date(2026, 3, 31)
                delta_dep_bump += 1

            if updates and commit:
                qs.update(**updates)

        # Report
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(
            f'Phase 1 (FY25): seeded={seeded} skipped={skipped} failed={failed}'
        ))
        self.stdout.write(self.style.SUCCESS(
            f'Phase 2 (March 2026 deltas): new={delta_new} disposed={delta_disposed} dep_bumped={delta_dep_bump}'
        ))
        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY-RUN — no rows written. Re-run with --commit to apply.'
            ))
        if errors:
            self.stdout.write(self.style.ERROR(f'\nErrors ({len(errors)}):'))
            for e in errors[:25]:
                self.stdout.write(self.style.ERROR(f'  ! {e}'))

    # ------------------------------------------------------------------
    def _ensure_categories(self, commit: bool) -> dict[str, AssetCategory]:
        """Create the 7 ADIC AssetCategories if missing. Return code → category."""
        out: dict[str, AssetCategory] = {}
        for code, (label, cost_c, accum_c, exp_c, months, method) in ADIC_CATEGORY_MAP.items():
            existing = AssetCategory.objects.filter(code=f'ADIC-{code}').first()
            if existing:
                out[code] = existing
                continue

            try:
                cost_acct  = Account.objects.get(code=cost_c)
                accum_acct = Account.objects.get(code=accum_c)
                exp_acct   = Account.objects.get(code=exp_c)
            except Account.DoesNotExist as exc:
                raise CommandError(
                    f'Category {code} {label}: missing GL account ({exc}). '
                    'Run setup_chart_of_accounts first.'
                )

            if not commit:
                # placeholder — return an unsaved AssetCategory just so the
                # phase-1 / phase-2 loops can resolve cat_code → cat for
                # counting purposes. We never write through it in dry-run.
                ph = AssetCategory(
                    code=f'ADIC-{code}',
                    name=label,
                    cost_account=cost_acct,
                    accum_depr_account=accum_acct,
                    depreciation_expense_account=exp_acct,
                    default_method=method,
                    default_useful_life_months=months,
                )
                out[code] = ph
                continue

            cat = AssetCategory.objects.create(
                code=f'ADIC-{code}',
                name=label,
                cost_account=cost_acct,
                accum_depr_account=accum_acct,
                depreciation_expense_account=exp_acct,
                default_method=method,
                default_useful_life_months=months,
                is_active=True,
                is_passenger_vehicle=(code == '210002'),
                default_capital_allowance_method=('reducing_balance' if code == '210002' else 'reducing_balance'),
                default_capital_allowance_rate=Decimal('25.00'),
                default_tax_cost_cap=(Decimal('175000.00') if code == '210002' else None),
            )
            self.stdout.write(self.style.SUCCESS(f'  + AssetCategory ADIC-{code} {label}'))
            out[code] = cat
        return out
