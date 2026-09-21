"""
Dedupe billing.Contact rows by (company, contact_type, normalised name).

A "duplicate" is two Contact rows where:
  * same owning company (entity-scoped — vendors/customers belong to ONE
    Alpha Direct subsidiary; the SAME name under a DIFFERENT company is a
    legitimately separate row and must NEVER be merged).
  * same contact_type (vendor stays separate from customer)
  * same normalised name — case-insensitive, trimmed, whitespace-collapsed,
    trailing punctuation removed.

For each duplicate cluster the keeper is the row with the most non-null
business fields filled (tax_id, email, phone, address). Ties break on
earliest created_at — the original row wins.

Every FK reference pointing at a duplicate is rewritten to point at the
keeper. We use Django's reverse-relation discovery so adding a new
ForeignKey to Contact in future code doesn't silently break this command.

Usage:
    python manage.py dedupe_contacts              # dry-run
    python manage.py dedupe_contacts --commit     # actually merge
    python manage.py dedupe_contacts --contact-type vendor --commit
"""
from __future__ import annotations

import re
from collections import defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

from billing.models import Contact


def _norm(name: str) -> str:
    """Normalise a contact name for dedup matching.

    Lower-cased, trimmed, internal whitespace collapsed, trailing punctuation
    stripped. Keeps & and () because they're meaningful in trading names.
    """
    if not name:
        return ''
    s = name.strip().lower()
    s = re.sub(r'\s+', ' ', s)
    s = s.rstrip('.,;:- ')
    # Common trailing tokens that obscure dupes: "(pty) ltd" vs "pty ltd"
    s = re.sub(r'\s*\(pty\)\s*', ' pty ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _completeness(c: Contact) -> int:
    """Score a Contact by how many useful fields are filled."""
    score = 0
    for field in ('tax_id', 'email', 'phone', 'address', 'currency_code_id',
                  'external_ref'):
        v = getattr(c, field, None)
        if v not in (None, ''):
            score += 1
    return score


class Command(BaseCommand):
    help = 'Merge duplicate billing.Contact rows by normalised name.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually write changes. Without it, dry-run.')
        parser.add_argument('--contact-type', default='',
                            help="Limit to one contact_type (e.g. 'vendor').")

    def handle(self, *args, **opts):
        commit = opts['commit']
        contact_type = opts.get('contact_type') or ''

        qs = Contact.objects.all()
        if contact_type:
            qs = qs.filter(contact_type=contact_type)

        # Bucket by (company, contact_type, normalised name). company_id is
        # part of the key so a vendor named "ABC Pty Ltd" under ADIC is never
        # merged into the same-named vendor under ADSA — entity isolation.
        buckets: dict[tuple, list[Contact]] = defaultdict(list)
        for c in qs.iterator():
            norm = _norm(c.name)
            if not norm:
                continue
            buckets[(c.company_id, c.contact_type or '', norm)].append(c)

        clusters = [v for v in buckets.values() if len(v) > 1]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Dedupe scan — {len(buckets)} unique keys, {len(clusters)} '
            f'clusters with duplicates.'
        ))
        if not clusters:
            return

        # Discover every FK pointing at Contact so we can rewrite them.
        contact_relations = [
            f for f in Contact._meta.get_fields()
            if f.is_relation and f.auto_created and not f.concrete
        ]

        total_merged = 0
        total_rewrites = 0
        errors: list[str] = []

        with transaction.atomic():
            sp = transaction.savepoint()

            for cluster in clusters:
                # Sort: best completeness first, then earliest created
                cluster.sort(
                    key=lambda c: (-_completeness(c), c.created_at),
                )
                keeper = cluster[0]
                losers = cluster[1:]
                self.stdout.write(
                    f"\nKEEPER  {keeper.contact_type}  {keeper.name!r}  "
                    f"(id={keeper.pk}, score={_completeness(keeper)})"
                )

                for loser in losers:
                    self.stdout.write(
                        f"  - merging {loser.name!r} (id={loser.pk}, "
                        f"score={_completeness(loser)})"
                    )

                    # Copy any non-null fields from the loser onto the keeper
                    # only when the keeper is missing them. Never overwrite.
                    for field in ('tax_id', 'email', 'phone', 'address',
                                  'external_ref'):
                        loser_val = getattr(loser, field, None)
                        if loser_val and not getattr(keeper, field, None):
                            setattr(keeper, field, loser_val)

                    # Rewrite every FK pointing at the loser → keeper.
                    for rel in contact_relations:
                        related_model = rel.related_model
                        field_name    = rel.field.name
                        try:
                            updated = related_model.objects.filter(
                                **{field_name: loser}
                            ).update(**{field_name: keeper})
                        except Exception as e:  # noqa: BLE001
                            errors.append(
                                f'{related_model.__name__}.{field_name}: {e}'
                            )
                            continue
                        if updated:
                            total_rewrites += updated
                            self.stdout.write(
                                f"      {related_model._meta.label}.{field_name}: "
                                f"{updated} rows pointed at keeper"
                            )

                    # Now safe to delete the loser
                    try:
                        loser.delete()
                        total_merged += 1
                    except Exception as e:  # noqa: BLE001
                        errors.append(
                            f"could not delete Contact {loser.pk} {loser.name!r}: {e}"
                        )

                # Persist the keeper if we copied any fields onto it.
                keeper.save()

            if commit:
                transaction.savepoint_commit(sp)
            else:
                transaction.savepoint_rollback(sp)

        marker = '' if commit else ' (DRY RUN — rolled back)'
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'DEDUPE COMPLETE{marker}'
        ))
        self.stdout.write(
            f'  clusters processed:  {len(clusters)}'
        )
        self.stdout.write(
            f'  rows merged/deleted: {total_merged}'
        )
        self.stdout.write(
            f'  FK rewrites:         {total_rewrites}'
        )
        if errors:
            self.stdout.write(self.style.ERROR(f'\nErrors ({len(errors)}):'))
            for e in errors[:30]:
                self.stdout.write(self.style.ERROR(f'  ! {e}'))
