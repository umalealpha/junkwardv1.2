"""
Read the recovery-possible flags Graphite pushes on the nightly feed, open the
subrogation case in Omni, and alert Lindani.

    python manage.py ingest_subrogation_flags [--dry-run]

WHAT IS OURS AND WHAT IS NOT
----------------------------
The flag itself is raised by the CLAIMS HANDLER, on the GRAPHITE claim, and
carried here by the existing nightly Graphite -> Omni feed
(`integrations/graphite_ingest.py`, POST /api/v1/graphite-ingest/, bearer
`GRAPHITE_INGEST_TOKEN`, 08:15). Adding the field to the Graphite claim and
adding it to that export is PRAMOD / THERISKCO WORK. Omni cannot add a field to
Graphite and must not try.

This command is the Omni half, written to the agreed shape so it works the day
the dataset starts arriving. Until then it finds no snapshot and exits quietly —
it is not an error for the feed to be silent.

THE DATASET CONTRACT (this is the answer to Pako's gap 2, "which fields must
Graphite push"). Dataset key: `subrogation_flags`. One flat row per flagged
claim:

    claim_reference        required. The Graphite claim number.
    recovery_possible      required. true / "yes" / 1 — anything else is skipped.
    incident_date          optional, YYYY-MM-DD. Date of loss; drives prescription.
    claim_paid_amount      optional, number. What we paid our insured.
    expected_recovery      optional, number. The handler's estimate.
    third_party_insurer    optional. The at-fault INSURER (a company, not a person).
    third_party_name       optional, and usually absent — see below.
    graphite_id            optional. Graphite's own row id, for traceability.

Note on the third party's NAME. The feed's Data-Protection screen rejects a
payload carrying anything that looks like personal data, and a named individual
is exactly that. So in practice Graphite pushes the claim, the money and the
at-fault insurer, and LINDANI COMPLETES THE PARTY DETAILS in Omni before the
letter goes out — which is what Pako's email describes her doing anyway. The
automation still earns its keep: it removes the lag between Claims spotting a
recovery and Finance being told.

The command is idempotent, because the feed is snapshot-replace and re-sends
the same rows every night. A claim already flagged is left alone: no second
alert, and no fresh 48 hours (see `flag_recovery_possible`).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from claims.models import Subrogation
from claims.subrogation_alert import flag_recovery_possible, notify_recovery_flagged

log = logging.getLogger(__name__)

DATASET = 'subrogation_flags'

_TRUE = {'true', 'yes', 'y', '1', 't'}


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v or '').strip().lower() in _TRUE


def _money(v):
    if v in (None, ''):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _a_date(v):
    if isinstance(v, date):
        return v
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


class Command(BaseCommand):
    help = 'Open subrogation cases from the Graphite recovery-possible flags and alert Lindani.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would happen; change nothing, send nothing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        from integrations.models import GraphiteSnapshot

        snap = GraphiteSnapshot.objects.filter(dataset=DATASET).first()
        if snap is None:
            self.stdout.write(
                f'No "{DATASET}" snapshot yet — the Graphite side of this is '
                f'TheRiskCo work and is not live. Nothing to do.')
            return

        rows = (snap.payload or {}).get('rows') or []
        if not isinstance(rows, list):
            self.stderr.write(f'Snapshot "{DATASET}" has no row list. Ignored.')
            return

        # The alert needs an author for the OmniTask and the case's created_by.
        # A real staff account is preferred; fall back to any superuser.
        author = (User.objects.filter(is_active=True, is_superuser=True)
                  .order_by('id').first())

        opened = alerted = skipped = 0
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            ref = str(row.get('claim_reference') or '').strip()
            if not ref or not _truthy(row.get('recovery_possible')):
                skipped += 1
                continue

            sub = Subrogation.objects.filter(claim_reference=ref).first()
            created = False
            if sub is None:
                if dry:
                    self.stdout.write(f'  would OPEN case for {ref}')
                    opened += 1
                    alerted += 1
                    continue
                if author is None:
                    self.stderr.write(
                        f'  {ref}: no user available to own the case — skipped.')
                    skipped += 1
                    continue
                sub = Subrogation.objects.create(
                    claim_reference=ref,
                    incident_date=_a_date(row.get('incident_date')),
                    third_party_name=str(row.get('third_party_name') or '').strip(),
                    third_party_insurer=str(row.get('third_party_insurer') or '').strip(),
                    claim_paid_amount=_money(row.get('claim_paid_amount')) or Decimal('0.00'),
                    expected_recovery=_money(row.get('expected_recovery')) or Decimal('0.00'),
                    graphite_id=str(row.get('graphite_id') or '').strip(),
                    status=Subrogation.Status.PENDING,
                    created_by=author,
                )
                created = True

            if dry:
                if sub.recovery_flagged_at is None:
                    self.stdout.write(f'  would FLAG + alert {ref}')
                    alerted += 1
                continue

            if flag_recovery_possible(sub, source='graphite'):
                alerted += 1
                try:
                    notify_recovery_flagged(sub)
                except Exception as exc:                        # noqa: BLE001
                    # The case is flagged either way — an email failure must not
                    # make the next run think the flag never arrived.
                    log.warning('Subrogation alert email failed for %s: %s', ref, exc)
            if created:
                opened += 1

        self.stdout.write(
            f'{"DRY RUN — " if dry else ""}{len(rows)} row(s): '
            f'{opened} case(s) opened, {alerted} newly flagged and alerted, '
            f'{skipped} skipped.')
