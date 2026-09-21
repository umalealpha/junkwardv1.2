"""Merge duplicate vendor rows that BOTH carry transactions.

CFO directive 2026-07-25: "merge". ``prune_duplicate_vendors`` deleted the empty
duplicates; the ones left over have real history on both rows, so deleting
either would destroy records. This repoints every reference onto one surviving
row and then removes the emptied duplicate.

SAFETY — this rewrites live financial references, so:

* duplicates are keyed on **(entity, normalised name)**, exactly as the prune
  command does. The same name in two entities is NOT a duplicate (CFO
  2026-05-18) and is never merged.
* the survivor is the row with the most references; ties keep the oldest.
* every reverse relation is walked. A plain FK is repointed. A relation that
  cannot be repointed safely (a one-to-one or a unique constraint that would
  collide) ABORTS that merge — the pair is reported for a human, never guessed
  at. The only exception is this module's own derived rows
  (ReconSupplierProfile / SupplierReconLine), which are rebuilt from source and
  so are dropped on the loser rather than blocking the merge.
* every merge runs in its own transaction: it fully succeeds or changes nothing.
* ``--dry-run`` is the default. ``--apply`` is required to write.
* the plan is written to ``--journal`` (default
  /tmp/merged_duplicate_vendors.json) so every repoint is auditable and
  reversible by hand.

    python manage.py merge_duplicate_vendors --dry-run
    python manage.py merge_duplicate_vendors --company ADIC --apply
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import OneToOneRel

from billing.models import Contact
from core.models import Company

from .prune_duplicate_vendors import normalise, total, transaction_counts

# Derived rows owned by this module: rebuilt by build_supplier_recon, so they may
# be dropped from the loser instead of blocking the merge.
DERIVED_ACCESSORS = {'recon_profile', 'recon_lines'}


class Command(BaseCommand):
    help = 'Merge same-entity duplicate vendors that both carry transactions.'

    def add_arguments(self, parser):
        parser.add_argument('--company', default=None)
        parser.add_argument('--apply', action='store_true',
                            help='Actually merge. Without this it is a dry run.')
        parser.add_argument('--journal',
                            default='/tmp/merged_duplicate_vendors.json',
                            help='Where to write the audit journal.')

    def handle(self, *args, **options):
        apply = options['apply']
        code = options['company']

        vendors = Contact.objects.filter(
            contact_type__in=[Contact.ContactType.VENDOR,
                              Contact.ContactType.BROKER,
                              Contact.ContactType.REINSURER],
        ).select_related('company')
        if code:
            company = Company.objects.filter(code__iexact=code).first()
            if company is None:
                self.stderr.write(self.style.ERROR(f"No company '{code}'."))
                return
            vendors = vendors.filter(company=company)

        groups: dict[tuple, list] = {}
        for v in vendors:
            groups.setdefault((v.company_id, normalise(v.name)), []).append(v)

        journal = []
        merged = blocked = 0

        for (_company_id, key), rows in sorted(groups.items(),
                                               key=lambda kv: str(kv[0])):
            if len(rows) < 2 or not key:
                continue

            scored = sorted(
                ((total(transaction_counts(r)), r.created_at, r) for r in rows),
                key=lambda x: (-x[0], x[1]),
            )
            survivor = scored[0][2]
            losers = [r for _c, _d, r in scored[1:]]
            entity = survivor.company.code if survivor.company_id else 'NO-ENTITY'

            self.stdout.write(
                f"\n{entity}  '{key}'  keeping \"{survivor.name}\"")

            for loser in losers:
                plan, problem = self._plan(loser, survivor)
                if problem:
                    blocked += 1
                    self.stdout.write(self.style.WARNING(
                        f"    BLOCKED \"{loser.name}\" - {problem}"))
                    continue

                moves = ', '.join(f"{k}:{v}" for k, v in plan.items() if v)
                if not apply:
                    self.stdout.write(
                        f"    would merge \"{loser.name}\" -> "
                        f"\"{survivor.name}\"  [{moves or 'nothing to move'}]")
                    journal.append({'entity': entity, 'key': key,
                                    'survivor': str(survivor.id),
                                    'survivor_name': survivor.name,
                                    'loser': str(loser.id),
                                    'loser_name': loser.name, 'moves': plan})
                    merged += 1
                    continue

                try:
                    with transaction.atomic():
                        self._execute(loser, survivor, plan)
                except Exception as exc:  # noqa: BLE001
                    blocked += 1
                    self.stderr.write(self.style.ERROR(
                        f"    FAILED  \"{loser.name}\" - {exc}"))
                    continue

                merged += 1
                journal.append({'entity': entity, 'key': key,
                                'survivor': str(survivor.id),
                                'survivor_name': survivor.name,
                                'loser': str(loser.id),
                                'loser_name': loser.name, 'moves': plan})
                self.stdout.write(self.style.SUCCESS(
                    f"    MERGED  \"{loser.name}\" -> \"{survivor.name}\"  "
                    f"[{moves or 'nothing to move'}]"))

        try:
            with open(options['journal'], 'w') as fh:
                json.dump(journal, fh, indent=1)
            self.stdout.write(f"\naudit journal: {options['journal']}")
        except OSError as exc:
            self.stderr.write(self.style.WARNING(
                f"could not write the journal: {exc}"))

        verb = 'Would merge' if not apply else 'Merged'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {merged} duplicate(s). {blocked} blocked for a human."))
        if not apply:
            self.stdout.write(self.style.WARNING(
                'Dry run - nothing written. Re-run with --apply.'))

    # ------------------------------------------------------------------ #
    def _plan(self, loser, survivor):
        """What would move, or a reason this merge must not be automated."""
        plan: dict[str, int] = {}
        for rel in loser._meta.related_objects:
            accessor = rel.get_accessor_name()
            field = rel.field
            model = rel.related_model
            label = f"{model._meta.app_label}.{model.__name__}"

            if accessor in DERIVED_ACCESSORS:
                plan[f"{label}(drop)"] = model.objects.filter(
                    **{field.name: loser}).count()
                continue

            if isinstance(rel, OneToOneRel):
                if model.objects.filter(**{field.name: loser}).exists():
                    return {}, (f"one-to-one {label} on both rows - merge by "
                                f"hand")
                continue

            n = model.objects.filter(**{field.name: loser}).count()
            if not n:
                continue

            # A unique/unique_together constraint involving this FK could
            # collide once both rows point at the survivor.
            uniques = [field.name in (getattr(model._meta, 'unique_together', ()) or ())]
            for cons in getattr(model._meta, 'constraints', []):
                fields = set(getattr(cons, 'fields', ()) or ())
                if field.name in fields or f"{field.name}_id" in fields:
                    uniques.append(True)
            for ut in (getattr(model._meta, 'unique_together', ()) or ()):
                if field.name in ut:
                    uniques.append(True)
            if any(uniques):
                return {}, (f"{label} has a uniqueness rule on this link - "
                            f"merging could collide; do it by hand")

            plan[label] = n
        return plan, None

    def _execute(self, loser, survivor, plan):
        # The classification must SURVIVE the merge. Dropping the loser's
        # profile when the survivor has none took the supplier out of scope
        # entirely, so its bills silently vanished from the board — P41,989.02
        # disappeared on the first prod run (2026-07-25). Carry the profile
        # across first, and only then treat the rest as derived.
        from ...models import ReconSupplierProfile
        loser_profile = ReconSupplierProfile.objects.filter(contact=loser).first()
        if loser_profile is not None:
            if ReconSupplierProfile.objects.filter(contact=survivor).exists():
                loser_profile.delete()        # survivor already classified
            else:
                loser_profile.contact = survivor
                loser_profile.save(update_fields=['contact', 'updated_at'])

        for rel in loser._meta.related_objects:
            accessor = rel.get_accessor_name()
            field = rel.field
            model = rel.related_model
            qs = model.objects.filter(**{field.name: loser})
            if accessor in DERIVED_ACCESSORS:
                qs.delete()          # rebuilt by build_supplier_recon
                continue
            qs.update(**{field.name: survivor})
        loser.delete()
