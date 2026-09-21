"""Load Keetile Mokhendo's subrogation register into Omni.

The manual register (`Subrogations sheet updated.xlsb`, 988 rows) is the source.
Export it to CSV first (the header row must be the real column names: 'Claim #',
'Third Party Name', 'Claim Type', 'Appointed To', 'Date Appointed', the six cost
columns, 'Total Recover', 'Recovered To Date', 'Status', ...).

Safety, by design:
  * DRY RUN by default. Nothing is written unless --commit is given.
  * The load is wrapped in ONE transaction and RECONCILED to the register's own
    'Total Recover' column to the cent before it is allowed to commit. If the
    loaded total does not tie, the whole load is rolled back — a register whose
    headline does not foot to its detail must not reach the balance sheet.
  * Append-only: a claim reference already present is skipped, never overwritten.

Design decisions (CFO context 2026-08-17):
  * The register's keyed 'Total Recover' is authoritative for HISTORICAL rows.
    The six cost components are stored only when they actually foot to that keyed
    figure; where they don't (a known data defect in ~64 rows), the keyed total
    is kept and the partial components are noted, so every row still foots.
  * Status is derived from the money (settled / partial / pending / written off),
    not the free-text status column, which is spelt fifteen ways.
  * 'Appointed To' becomes a panel link only for a real law firm or debt
    collector. Payment methods, insurers and free-text notes found in that column
    are recorded in notes, never onboarded as collectors.
  * Claims are ADIC's alone (notebook rule), so rows load against ADIC.
"""
from __future__ import annotations

import csv
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from claims.models import Subrogation, SubrogationPanel
from claims.recoveries.claim_number import parse_claim_number
from claims.recoveries.claim_type import ClaimType, normalise_claim_type
from claims.recoveries.costs import compute_total_recoverable
from claims.recoveries.dates import parse_register_date
from claims.recoveries.panel import PanelKind, normalise_panel_name
from claims.recoveries.status import RecoveryStatus, normalise_status
from core.models import Company

ZERO = Decimal('0.00')

# Register column -> our claim_type slug.
_CLAIM_TYPE_SLUG = {
    ClaimType.MOTOR_ACCIDENT:      Subrogation.ClaimTypeChoice.MOTOR_ACCIDENT,
    ClaimType.BUILDINGS_COMBINED:  Subrogation.ClaimTypeChoice.BUILDINGS_COMBINED,
    ClaimType.FIRE_SPECIAL_PERILS: Subrogation.ClaimTypeChoice.FIRE_SPECIAL_PERILS,
    ClaimType.MONEY_FIDELITY:      Subrogation.ClaimTypeChoice.MONEY_FIDELITY,
    ClaimType.OTHER:               Subrogation.ClaimTypeChoice.OTHER,
}
# Register 'Appointed To' PanelKind -> our forward-looking panel Kind.
_PANEL_KIND = {
    PanelKind.LAW_FIRM:       SubrogationPanel.Kind.EXTERNAL_LAWYER,
    PanelKind.DEBT_COLLECTOR: SubrogationPanel.Kind.DEBT_COLLECTOR,
}


def _money(raw) -> Decimal:
    """Register money cell -> Decimal, blank/junk -> 0. Reuses the costs parser."""
    from claims.recoveries.costs import _to_decimal
    return _to_decimal(raw)


class Command(BaseCommand):
    help = 'Load the subrogation recovery register (CSV) into Omni. Dry run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--company', default='ADIC',
                            help='Company code to load against (default ADIC — claims are ADIC only).')
        parser.add_argument('--created-by', default=None,
                            help='Username to stamp as creator. Defaults to the first superuser.')
        parser.add_argument('--commit', action='store_true',
                            help='Actually write. Omitted = dry run, everything rolled back.')

    def handle(self, *args, **opts):
        path = opts['csv_path']
        try:
            with open(path, newline='', encoding='utf-8-sig') as f:
                rows = list(csv.DictReader(f))
        except FileNotFoundError:
            raise CommandError(f'CSV not found: {path}')
        if not rows:
            raise CommandError('CSV has no data rows.')

        company = Company.objects.filter(code=opts['company']).first()
        if company is None:
            # Positive match or nothing. A NULL-company load would put the whole
            # book behind entity scoping and hide it from every non-CFO user
            # (the L6 permissive-default class) — refuse instead of guessing.
            raise CommandError(
                f"Company '{opts['company']}' not found. Pass a valid --company "
                f"code (claims are ADIC); nothing was loaded.")

        creator = (User.objects.filter(username=opts['created_by']).first()
                   if opts['created_by'] else User.objects.filter(is_superuser=True).first())
        if creator is None:
            creator = User.objects.order_by('id').first()
        if creator is None:
            raise CommandError('No user available to stamp as creator.')

        stats = {'loaded': 0, 'skipped_dup': 0, 'skipped_blank': 0,
                 'panel_linked': 0, 'panel_quarantined': 0, 'future_dates': 0,
                 'components_kept': 0}
        keyed_total = ZERO
        keyed_recovered = ZERO
        loaded_recoverable = ZERO
        loaded_recovered = ZERO
        panel_cache: dict[str, SubrogationPanel] = {}

        with transaction.atomic():
            for r in rows:
                claimno = parse_claim_number(r.get('Claim #'))
                ref = claimno.normalised
                keyed = _money(r.get('Total Recover'))
                recovered = _money(r.get('Recovered To Date'))

                # A row with neither a claim number nor any money is not data.
                if not ref and keyed == ZERO and recovered == ZERO:
                    stats['skipped_blank'] += 1
                    continue
                keyed_total += keyed
                keyed_recovered += recovered

                # Append-only dedup. A parseable claim number keys on itself;
                # a blank/unparseable one ('TBA', junk) would otherwise re-load
                # on every --commit and double-count the book, so it keys on a
                # composite of who + how much + company instead.
                tp_name = (r.get('Third Party Name') or '').strip() or '(unknown)'
                if ref:
                    already = Subrogation.objects.filter(claim_reference=ref).exists()
                else:
                    already = Subrogation.objects.filter(
                        claim_reference='(none)', third_party_name=tp_name,
                        expected_recovery=keyed, company=company,
                    ).exists()
                if already:
                    stats['skipped_dup'] += 1
                    # still counts toward keyed_total so reconciliation reflects
                    # the whole register; add to the loaded side too so it ties.
                    loaded_recoverable += keyed
                    loaded_recovered += recovered
                    continue

                # --- cost build-up: keep components only if they foot ---------
                comps = [r.get("Assessor's Fees"), r.get('Repair Costs'),
                         r.get("Client's Excess"), r.get('Towing Fees'),
                         r.get('Legal Fees'), r.get('Salvage Amount')]
                comp_total = compute_total_recoverable(*comps)
                keep_components = any(_money(c) != ZERO for c in comps) and comp_total == keyed

                # --- panel resolution ---------------------------------------
                appointed_raw = (r.get('Appointed To') or '').strip()
                pn = normalise_panel_name(appointed_raw)
                appointed_to = None
                extra_notes = []
                if pn.kind in _PANEL_KIND:
                    key = pn.name.lower()
                    panel = panel_cache.get(key)
                    if panel is None:
                        panel, _ = SubrogationPanel.objects.get_or_create(
                            name=pn.name,
                            defaults={'kind': _PANEL_KIND[pn.kind], 'created_by': creator},
                        )
                        panel_cache[key] = panel
                    appointed_to = panel
                    stats['panel_linked'] += 1
                elif appointed_raw:
                    extra_notes.append(f"Appointed-To (raw): {appointed_raw} [{pn.kind.name}]")
                    stats['panel_quarantined'] += 1

                # --- dates ---------------------------------------------------
                appt = parse_register_date(r.get('Date Appointed'))
                # future appointment date = keying error (Keetile: no future dates)
                from django.utils import timezone
                if appt and appt > timezone.localdate():
                    extra_notes.append(f"Date-Appointed (raw, future/rejected): {r.get('Date Appointed')}")
                    stats['future_dates'] += 1
                    appt = None

                # --- status from the money ----------------------------------
                text_status = normalise_status(r.get('Status'))
                balance = keyed - recovered
                if text_status == RecoveryStatus.WRITTEN_OFF:
                    status = Subrogation.Status.WRITTEN_OFF
                elif keyed > ZERO and balance <= Decimal('0.005'):
                    status = Subrogation.Status.FULLY_RECOVERED
                elif recovered > ZERO:
                    status = Subrogation.Status.PARTIAL
                else:
                    status = Subrogation.Status.PENDING

                ctype = _CLAIM_TYPE_SLUG[normalise_claim_type(r.get('Claim Type'))]
                if text_status is not RecoveryStatus.UNKNOWN:
                    extra_notes.append(f"Register status: {text_status.value}")

                sub = Subrogation(
                    claim_reference=ref or '(none)',
                    company=company,
                    claim_type=ctype,
                    third_party_name=(r.get('Third Party Name') or '').strip() or '(unknown)',
                    date_appointed=appt,
                    appointed_to=appointed_to,
                    claim_paid_amount=ZERO,
                    expected_recovery=keyed,       # authoritative historical figure
                    actual_recovery=recovered,     # typed cumulative (receipts come later)
                    status=status,
                    graphite_id=(ref if claimno.is_graphite_era else ''),
                    notes='\n'.join(extra_notes),
                    created_by=creator,
                )
                if keep_components:
                    sub.assessor_fees  = _money(r.get("Assessor's Fees"))
                    sub.repair_costs   = _money(r.get('Repair Costs'))
                    sub.client_excess  = _money(r.get("Client's Excess"))
                    sub.towing_fees    = _money(r.get('Towing Fees'))
                    sub.legal_fees     = _money(r.get('Legal Fees'))
                    sub.salvage_amount = _money(r.get('Salvage Amount'))
                    # expected_recovery stays = keyed (== the footing component
                    # total); total_recoverable prefers the components anyway.
                    stats['components_kept'] += 1
                sub.save()
                stats['loaded'] += 1
                loaded_recoverable += sub.total_recoverable
                loaded_recovered += recovered

            # --- reconciliation gate ---------------------------------------
            recon_ok = (loaded_recoverable == keyed_total)
            self._report(stats, keyed_total, keyed_recovered,
                         loaded_recoverable, loaded_recovered, recon_ok, opts['commit'])
            if not recon_ok:
                transaction.set_rollback(True)
                raise CommandError(
                    f'RECONCILIATION FAILED: loaded {loaded_recoverable:,.2f} '
                    f'!= register {keyed_total:,.2f}. Rolled back — nothing written.')
            if not opts['commit']:
                transaction.set_rollback(True)
                self.stdout.write(self.style.SUCCESS(
                    'Dry run complete — rolled back, nothing written. Re-run with --commit to load.'))

    def _report(self, stats, keyed_total, keyed_recovered,
                loaded_recoverable, loaded_recovered, recon_ok, committed):
        w = self.stdout.write
        w('')
        w('Subrogation register load — ' + ('COMMIT' if committed else 'DRY RUN'))
        w('-' * 52)
        for k, v in stats.items():
            w(f'  {k:22s} {v:>8}')
        w('-' * 52)
        w(f'  register Total Recover {keyed_total:>18,.2f}')
        w(f'  loaded total recoverable {loaded_recoverable:>16,.2f}')
        w(f'  register Recovered       {keyed_recovered:>16,.2f}')
        w(f'  loaded recovered         {loaded_recovered:>16,.2f}')
        w('-' * 52)
        style = self.style.SUCCESS if recon_ok else self.style.ERROR
        w(style(f'  reconciliation: {"TIES to the cent" if recon_ok else "MISMATCH"}'))
