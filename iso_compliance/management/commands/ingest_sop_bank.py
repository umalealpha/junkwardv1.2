"""ingest_sop_bank — load the Data Room SOP tree into the SOP Bank.

    python manage.py ingest_sop_bank --root /tmp/sop [--manifest /tmp/sop/manifest.json]

Layout expectation (mirrors the Projects Data Room):
    <root>/<Department>/.../<file>.docx|pdf|xlsx|webm|mp4
The first-level folder is the department. A department literally named
"Obsolete" ingests with status=obsolete.

Idempotent: rows key on source_path (the path relative to --root). Re-running
updates metadata + text and re-copies the file only when the size changed.

The optional manifest (built off the Mastersheet + the omni-alignment
review) carries per-file metadata keyed by that same relative path:
    {"<rel_path>": {"sop_number": "...", "owner": "...",
                     "revision_date": "YYYY-MM-DD", "iso_compliant": null,
                     "omni_fit": "gap", "omni_fit_notes": "..."}}
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import zipfile

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from iso_compliance.models import SOPDocument

INGEST_EXTS = {'.docx', '.pdf', '.xlsx', '.webm', '.mp4', '.doc', '.pptx'}
_TAG_RE = re.compile(r'<[^>]+>')


def extract_docx_text(path: str, limit: int = 200_000) -> str:
    """Plain text from a .docx without extra dependencies: the document XML
    with tags stripped. Good enough for icontains search."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read('word/document.xml').decode('utf-8', 'ignore')
    except (zipfile.BadZipFile, KeyError, OSError):
        return ''
    # Paragraph + break tags become newlines so words don't fuse.
    xml = re.sub(r'</w:p>|<w:br[^>]*/>', '\n', xml)
    text = _TAG_RE.sub('', xml)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text[:limit]


class Command(BaseCommand):
    help = 'Ingest the SOP document tree into the SOP Bank (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--root', required=True)
        parser.add_argument('--manifest', default='')

    def handle(self, *args, **opts):
        root = os.path.abspath(opts['root'])
        if not os.path.isdir(root):
            raise CommandError(f'--root {root} is not a directory')

        meta: dict = {}
        if opts['manifest']:
            with open(opts['manifest'], encoding='utf-8') as f:
                meta = json.load(f)

        created = updated = skipped = 0
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in INGEST_EXTS:
                    skipped += 1
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace('\\', '/')
                parts = rel.split('/')
                if len(parts) < 2:      # file at root level - no department
                    skipped += 1
                    continue
                department = parts[0]
                size = os.path.getsize(full)
                m = meta.get(rel, {})

                rev = None
                if m.get('revision_date'):
                    try:
                        rev = dt.date.fromisoformat(m['revision_date'])
                    except ValueError:
                        rev = None

                defaults = {
                    'department':    department,
                    'title':         m.get('title') or os.path.splitext(fn)[0],
                    'sop_number':    str(m.get('sop_number') or ''),
                    'owner':         m.get('owner') or '',
                    'revision_date': rev,
                    'iso_compliant': m.get('iso_compliant'),
                    'file_type':     ext.lstrip('.'),
                    # Obsolete if ANY folder on the path is named Obsolete —
                    # departments keep their own Obsolete/OBSOLETE subfolders.
                    'status':        (SOPDocument.STATUS_OBSOLETE
                                      if any(p.lower() == 'obsolete' for p in parts[:-1])
                                      else SOPDocument.STATUS_ACTIVE),
                    'omni_fit':      m.get('omni_fit') or SOPDocument.OMNI_FIT_UNREVIEWED,
                    'omni_fit_notes': m.get('omni_fit_notes') or '',
                }
                sop, was_created = SOPDocument.objects.get_or_create(
                    source_path=rel, defaults=defaults)
                if not was_created:
                    for k, v in defaults.items():
                        setattr(sop, k, v)

                if ext == '.docx':
                    sop.content_text = extract_docx_text(full)

                if was_created or sop.size_bytes != size or not sop.file:
                    with open(full, 'rb') as fh:
                        sop.file.save(fn, File(fh), save=False)
                    sop.size_bytes = size
                sop.save()
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

        self.stdout.write(self.style.SUCCESS(
            f'SOP Bank ingest done: created={created} updated={updated} '
            f'skipped={skipped} total_rows={SOPDocument.objects.count()}'))
