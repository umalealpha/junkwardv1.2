"""
Load the blank claim forms into Omni's Claim Forms Vault.

Reads a directory of PDFs plus a manifest.json (slug -> title -> source) — the
same canonical set held in the Graphite repo at backend/resources/claim-forms/.
Point --dir at a copy of that folder.

Idempotent: matches on slug, replaces the file and title in place, so re-running
after a form is updated republishes it without creating duplicates. Categories
are inferred from the slug; override in the admin if needed.

    python manage.py load_claim_forms --dir /path/to/claim-forms
"""

import json
import os

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from claims.vault_models import ClaimForm

# slug -> category. Anything unlisted falls to 'other'.
_CATEGORY = {
    'motor-accident': 'motor', 'glass': 'motor', 'locks-and-keys': 'motor',
    'property-loss': 'property', 'burglary': 'property', 'fire': 'property',
    'business-interruption': 'property', 'all-risk': 'property',
    'mobile-electronic-device': 'property', 'goods-in-transit': 'property',
    'money': 'property', 'defective-workmanship': 'property',
    'liability': 'liability', 'directors-officers-liability': 'liability',
    'professional-indemnity': 'liability', 'fidelity': 'liability',
    'contractors-all-risks': 'engineering', 'erection-all-risk': 'engineering',
    'plant-all-risks': 'engineering', 'machinery-breakdown': 'engineering',
    'machinery-breakdown-loss-of-profit': 'engineering',
    'electronic-equipment': 'engineering',
    'marine-cargo-once-off': 'marine', 'marine-cargo-open': 'marine',
    'hospital-cash-back': 'life_health', 'accidental-death': 'life_health',
    'medical-malpractice': 'life_health', 'legal': 'life_health', 'bonu-legal': 'life_health',
    'travel': 'specialty', 'workmens-compensation': 'specialty',
}


class Command(BaseCommand):
    help = 'Load blank claim forms into the Omni Claim Forms Vault from a folder'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dir', default=None,
            help='folder of PDFs + manifest.json (defaults to claims/seed_forms in the repo)')

    def handle(self, *args, **opts):
        import claims
        default_dir = os.path.join(os.path.dirname(claims.__file__), 'seed_forms')
        d = opts['dir'] or default_dir
        if not os.path.isdir(d):
            # Raise, don't return: a silent exit-0 would let a deploy step think
            # the vault was seeded when it was not (checklist H6).
            raise CommandError(f'No such directory: {d}')

        manifest_path = os.path.join(d, 'manifest.json')
        titles, sources = {}, {}
        if os.path.exists(manifest_path):
            for row in json.load(open(manifest_path)):
                titles[row['file']] = row.get('title', row['file'])
                sources[row['file']] = row.get('source_original', '')

        added = updated = 0
        order = 10
        for name in sorted(os.listdir(d)):
            if not name.lower().endswith('.pdf'):
                continue
            slug = name[:-4]  # strip .pdf
            title = titles.get(name) or slug.replace('-', ' ').title()
            category = _CATEGORY.get(slug, 'other')

            form, created = ClaimForm.objects.get_or_create(
                slug=slug, defaults={'title': title, 'sort_order': order})
            form.title = title
            form.category = category
            form.source_name = sources.get(name, '')
            form.sort_order = order
            form.active = True
            with open(os.path.join(d, name), 'rb') as fh:
                form.file.save(name, File(fh), save=False)
            form.save()

            order += 10
            if created:
                added += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Claim Forms Vault: {added} added, {updated} updated. '
            f'Total now {ClaimForm.objects.count()}.'))
