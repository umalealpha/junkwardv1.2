"""
claims.0005_backfill_salvage_into_salvage_app

Deprecation step for `claims.Salvage` — the canonical salvage record now
lives in the `salvage` app. CFO directive 2026-05-24.

This migration:

  1. Adds `claims.Salvage.linked_salvage_item` FK → salvage.SalvageItem.
  2. For every existing `claims.Salvage` row, ensures a matching
     `salvage.SalvageItem` exists keyed on
     (claim_reference, asset_description) and sets `linked_salvage_item`
     to point at it.

Reversible: forward inserts new salvage.SalvageItem rows only when a
match doesn't already exist; reverse unsets `linked_salvage_item` but
deliberately leaves any inserted salvage.SalvageItem rows in place
(they may have accreted their own history — bids, inspections, JE
links — after the forward migration ran).

The legacy `claims.Salvage` rows are NOT deleted — incoming FKs and
audit history still need to be unwound first.
"""
import re
import uuid

from django.db import migrations, models


_SLUG_RE = re.compile(r'[^A-Za-z0-9]+')


def _make_item_code(claim_reference: str, asset_description: str) -> str:
    """Stable, < 40-char item_code derived from claim_reference.

    Format: LEG-<claim-slug>[-<hash>]. We keep enough of the claim
    reference to be human-readable; the hash suffix only appears when
    we need to dedupe across multiple legacy rows pointing at the same
    claim_reference.
    """
    base = _SLUG_RE.sub('-', (claim_reference or '').strip()).strip('-').upper()
    if not base:
        base = 'NOREF'
    base = base[:30]
    return f'LEG-{base}'[:40]


def _unique_item_code(SalvageItem, claim_reference: str,
                      asset_description: str, used: set) -> str:
    """Generate an item_code that doesn't collide with the table or set."""
    candidate = _make_item_code(claim_reference, asset_description)
    if candidate not in used and not SalvageItem.objects.filter(
        item_code=candidate,
    ).exists():
        used.add(candidate)
        return candidate

    # Walk a numeric suffix until we find a free slot. Bounded loop —
    # any real-world legacy data set will resolve in O(10) iterations.
    for i in range(2, 1000):
        suffix = f'-{i}'
        cap = max(1, 40 - len(suffix))
        cand = (candidate[:cap] + suffix)[:40]
        if cand not in used and not SalvageItem.objects.filter(
            item_code=cand,
        ).exists():
            used.add(cand)
            return cand

    # Last-resort hash tail — pathological case.
    tail = uuid.uuid4().hex[:6]
    cap = max(1, 40 - len(tail) - 1)
    return f'{candidate[:cap]}-{tail}'[:40]


def forwards(apps, schema_editor):
    ClaimsSalvage = apps.get_model('claims', 'Salvage')
    SalvageItem   = apps.get_model('salvage', 'SalvageItem')

    used_codes = set()
    for legacy in ClaimsSalvage.objects.all().iterator():
        if legacy.linked_salvage_item_id:
            continue

        # Match on (claim_reference, asset_description). asset_description
        # is up to 300 chars, so compare against the first 200 of
        # SalvageItem.part_name where we'll have stashed it.
        target = SalvageItem.objects.filter(
            claim_number=legacy.claim_reference or '',
            part_name=(legacy.asset_description or '')[:200],
        ).first()

        if target is None:
            item_code = _unique_item_code(
                SalvageItem, legacy.claim_reference or '',
                legacy.asset_description or '', used_codes,
            )
            # Map legacy status → salvage.SalvageItem.Status (text choices).
            # Conservative mapping — anything unknown stays AVAILABLE so
            # the row is visible to the yard team for cleanup.
            status_map = {
                'pending':   'available',
                'for_sale':  'available',
                'sold':      'sold',
                'scrapped':  'scrapped',
                'retained':  'on_hold',
            }
            new_status = status_map.get(legacy.status, 'available')

            target = SalvageItem.objects.create(
                item_code        = item_code,
                claim_number     = (legacy.claim_reference or '')[:60],
                part_name        = (legacy.asset_description or 'Legacy salvage')[:200],
                part_description = legacy.notes or '',
                quantity         = 1,
                condition        = 'fair',
                asking_price     = legacy.sale_proceeds or 0,
                reserve_price    = legacy.estimated_value or 0,
                cost_basis       = legacy.estimated_value or 0,
                status           = new_status,
                location         = '',
                received_date    = legacy.incident_date,
                sold_date        = legacy.sale_date,
                company_id       = legacy.company_id,
                received_by_id   = legacy.created_by_id,
                created_by_id    = legacy.created_by_id,
                notes            = (
                    f'[Backfilled from claims.Salvage {legacy.pk} on '
                    f'2026-05-24] {legacy.notes or ""}'
                )[:5000],
            )

        legacy.linked_salvage_item_id = target.pk
        legacy.save(update_fields=['linked_salvage_item'])


def backwards(apps, schema_editor):
    """Unset the pointer; leave any inserted SalvageItem rows in place."""
    ClaimsSalvage = apps.get_model('claims', 'Salvage')
    ClaimsSalvage.objects.exclude(
        linked_salvage_item__isnull=True,
    ).update(linked_salvage_item=None)


class Migration(migrations.Migration):

    dependencies = [
        ('claims',  '0004_alter_recoveryimportbatch_status'),
        ('salvage', '0005_auction'),
    ]

    operations = [
        migrations.AddField(
            model_name='salvage',
            name='linked_salvage_item',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.SET_NULL,
                related_name='legacy_claims_salvages',
                to='salvage.salvageitem',
                help_text=(
                    'Pointer to the salvage.SalvageItem row that '
                    'supersedes this legacy entry.'
                ),
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
