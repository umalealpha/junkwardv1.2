"""Repair plan-pack files that were stored as gzip blobs under their real name.

Files gzipped to get past the Cloudflare WAF were saved compressed, so the
download endpoint hands back binary under an `.html` name. New uploads are
normalised at ingest (see budgets/uploads.py); this fixes the rows already
stored that way.

    python manage.py fix_gzipped_plan_files --dry-run
    python manage.py fix_gzipped_plan_files
"""
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from budgets.models import PlanPackFile
from budgets.uploads import _inflate, looks_gzipped


class Command(BaseCommand):
    help = 'Decompress plan-pack files that were stored gzip-compressed.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change and write nothing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        if dry:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing will be written.'))

        fixed = skipped = failed = 0

        for pf in PlanPackFile.objects.select_related('pack').order_by('id'):
            if not pf.file:
                continue
            short = pf.file.name.split('/')[-1]

            try:
                pf.file.open('rb')
                raw = pf.file.read()
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  READ FAIL  {short}: {exc}'))
                failed += 1
                continue
            finally:
                try:
                    pf.file.close()
                except Exception:
                    pass

            if not looks_gzipped(raw[:2]):
                skipped += 1
                continue

            if short.lower().endswith(('.gz', '.tgz', '.tar.gz')):
                self.stdout.write(f'  KEEP       {short} (genuine archive)')
                skipped += 1
                continue

            out = _inflate(raw)
            if out is None:
                self.stdout.write(self.style.ERROR(f'  INFLATE FAIL  {short}'))
                failed += 1
                continue

            self.stdout.write(
                f'  {"WOULD FIX " if dry else "FIXED     "} {short}: '
                f'{len(raw):,} → {len(out):,} bytes')

            if not dry:
                # save() writes a new object and repoints the field; the old
                # compressed blob is left in place rather than deleted, so this
                # is reversible if anything looks wrong.
                pf.file.save(short, ContentFile(out), save=True)

            fixed += 1

        self.stdout.write('')
        verb = 'would fix' if dry else 'fixed'
        self.stdout.write(self.style.SUCCESS(
            f'{verb}: {fixed}   already fine: {skipped}   failed: {failed}'))
