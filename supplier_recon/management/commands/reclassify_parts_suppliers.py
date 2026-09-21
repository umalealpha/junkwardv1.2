"""Reclassify named suppliers from panel beater to parts supplier.

Bharath's feedback 2026-07-27 (point 4): some parts suppliers are showing as
panel beaters — he named Naledi Motors and CFAO Mobility. The name-based
``refine_supplier_types`` guesses "motors / mobility / auto" as a panel beater,
which is right for a body shop but wrong for a dealership that supplies parts.
Those are genuine judgement calls a name cannot settle, so this corrects only
the specific suppliers Bharath named rather than rewriting the keyword rules
(which would wrongly flip every real panel beater whose name contains "motors").

Both panel_beater and parts are claim-backed, so this never changes whether the
claim-authorisation rule fires — it only relabels the trade shown on the board.

    python manage.py reclassify_parts_suppliers --dry-run
    python manage.py reclassify_parts_suppliers
    python manage.py reclassify_parts_suppliers --company ADIC

Idempotent: a supplier already set to parts (by this command or a person) is
skipped. Other suppliers Bharath may want moved are corrected in Django admin
(Recon Supplier Profiles) — the documented path for these assumptions.
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Company

from ...constants import SupplierCategory
from ...models import ReconSupplierProfile

RECLASSIFIED_NOTE = ('Reclassified panel beater -> parts supplier per Bharath '
                     '2026-07-27 (point 4).')

# Case-insensitive substrings identifying the suppliers Bharath named. Kept as a
# short explicit list on purpose — this is a correction of specific vendors, not
# a rule change.
TARGET_NAME_FRAGMENTS = [
    'naledi motors',
    'cfao',
]


class Command(BaseCommand):
    help = ("Move the parts suppliers Bharath named (Naledi Motors, CFAO) from "
            "panel beater to parts supplier.")

    def add_arguments(self, parser):
        parser.add_argument('--company', default=None,
                            help='Company code to scope to, e.g. ADIC.')
        parser.add_argument('--all', action='store_true', dest='all_entities',
                            help='Apply across EVERY entity. Required if no '
                                 '--company is given, so a cross-entity change '
                                 'is always a deliberate choice.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change, change nothing.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        code = options['company']

        # Entity isolation: never touch every entity by accident. A run must
        # either name a company or explicitly opt into all of them (DeepSeek
        # review 2026-07-27). Both categories are claim-backed so this only
        # relabels a trade, but a supplier can be a genuine panel beater in one
        # entity and a parts dealer in another.
        if not code and not options['all_entities']:
            raise CommandError(
                'Scope the run: pass --company ADIC (or another code), or '
                '--all to apply across every entity on purpose.')

        qs = (ReconSupplierProfile.objects
              .select_related('contact')
              .order_by('contact__name'))
        if code:
            company = Company.objects.filter(code__iexact=code).first()
            if company is None:
                self.stderr.write(self.style.ERROR(f"No company '{code}'."))
                return
            qs = qs.filter(contact__company=company)

        changed = skipped = 0
        for profile in qs:
            name = (profile.contact.name or '').lower()
            if not any(frag in name for frag in TARGET_NAME_FRAGMENTS):
                continue
            if profile.category == SupplierCategory.PARTS:
                skipped += 1
                self.stdout.write(
                    f"  already parts  {profile.contact.name[:52]}")
                continue
            changed += 1
            verb = 'would move' if dry else 'moved'
            self.stdout.write(
                f"  {verb:10s} {profile.contact.name[:52]:52s} "
                f"{profile.category} -> parts")
            if not dry:
                profile.category = SupplierCategory.PARTS
                note = (profile.notes or '').strip()
                profile.notes = f"{note} {RECLASSIFIED_NOTE}".strip()
                profile.save(update_fields=['category', 'notes', 'updated_at'])

        verb = 'Would reclassify' if dry else 'Reclassified'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {changed} supplier(s); {skipped} already parts."))
        if dry:
            self.stdout.write(self.style.WARNING('Dry run - nothing written.'))
        elif changed:
            self.stdout.write(
                'Rebuild the affected month(s) so the board picks up the new '
                'trade, or it updates on the next scheduled build.')
