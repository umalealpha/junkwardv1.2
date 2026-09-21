"""Upload normalisation for plan-pack files.

Cloudflare's WAF rejects HTML and JS that contain script tags, so files are
sometimes gzip-compressed before upload to get them past it. The compressed
bytes then land under the original name — an `.html` file whose content is a
gzip blob — and every consumer downstream breaks: the download endpoint hands
the browser binary, and anything that tries to render it sees noise.

Serving those rows with `Content-Encoding: gzip` papers over it for one code
path and leaves the stored artefact wrong for all the others. Decompressing at
ingest means what we store is the real file.
"""
from __future__ import annotations

import gzip
import io
import logging

from django.core.files.base import ContentFile

log = logging.getLogger(__name__)

GZIP_MAGIC = b'\x1f\x8b'

# A gzip member can expand enormously from very few bytes, and this runs on an
# authenticated upload before anything else looks at the file. Refuse to
# inflate past this and keep the original bytes instead.
MAX_DECOMPRESSED_BYTES = 40 * 1024 * 1024


def looks_gzipped(head: bytes) -> bool:
    return head[:2] == GZIP_MAGIC


def _inflate(raw: bytes) -> bytes | None:
    """Return decompressed bytes, or None if this is not usable gzip."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            out = gz.read(MAX_DECOMPRESSED_BYTES + 1)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        log.warning('plan-pack upload: gzip magic present but inflate failed (%s)', exc)
        return None

    if len(out) > MAX_DECOMPRESSED_BYTES:
        log.warning('plan-pack upload: refusing to inflate past %d bytes', MAX_DECOMPRESSED_BYTES)
        return None
    if not out:
        return None
    return out


def normalise_upload(uploaded):
    """Decompress a gzipped upload so the stored file is the real file.

    Returns (django_file, note). `note` is a short human string when something
    was changed, otherwise ''. The original upload is returned untouched when it
    is not gzip, when inflating fails, or when the caller clearly meant to store
    a genuine archive (`.gz` is only stripped, never left compressed).
    """
    name = (getattr(uploaded, 'name', '') or '').strip()

    # Peek the magic bytes only. A plain 100MB workbook must not be pulled into
    # memory just to learn it is not gzip.
    try:
        uploaded.seek(0)
        head = uploaded.read(2)
        uploaded.seek(0)
    except Exception:                                    # pragma: no cover - defensive
        # Keep the uploader's bytes, but never let a broken normaliser go quiet —
        # otherwise gzipped files silently revert to being stored as blobs.
        log.warning('plan-pack upload: could not read %r for normalisation',
                    name, exc_info=True)
        return uploaded, ''

    if not looks_gzipped(head):
        return uploaded, ''

    # A real .tar.gz / .gz archive is a legitimate thing to keep as-is.
    lowered = name.lower()
    if lowered.endswith(('.tar.gz', '.tgz')):
        return uploaded, ''

    try:
        uploaded.seek(0)
        raw = uploaded.read()
    except Exception:                                    # pragma: no cover - defensive
        log.warning('plan-pack upload: could not read %r for normalisation',
                    name, exc_info=True)
        return uploaded, ''
    finally:
        try:
            uploaded.seek(0)
        except Exception:                                # pragma: no cover - defensive
            pass

    out = _inflate(raw)
    if out is None:
        return uploaded, ''

    # foo.html.gz -> foo.html ; foo.html (gzip inside) -> foo.html
    new_name = name[:-3] if lowered.endswith('.gz') else name
    note = f'decompressed on upload ({len(raw):,} → {len(out):,} bytes)'
    return ContentFile(out, name=new_name or 'upload'), note
