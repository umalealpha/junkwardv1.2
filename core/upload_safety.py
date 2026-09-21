"""
core/upload_safety.py — content-based validation for uploaded documents.

Manus QC round, 2026-08-26 (P0): the recruitment CV upload trusted the FILENAME
only, so an executable renamed `cv.pdf` was accepted and stored; the internal HR
upload checked nothing at all. A filename is attacker-controlled and proves
nothing — this module looks at the actual first bytes instead.

Deliberately dependency-free (no python-magic / libmagic): the signatures we
need are short and stable, and adding a C library to the image for four magic
numbers is not worth the deploy risk.

NOTE — this is FORMAT validation, not anti-virus. It stops the wrong KIND of
file (an .exe posing as a CV); it does not inspect a genuine PDF for a malicious
payload. Real AV needs a clamd service on the host, which Omni does not run
today. See validate_document_upload's docstring.
"""
from __future__ import annotations

import io
import zipfile

# How many bytes we need to identify any supported type.
HEAD_BYTES = 8

# Document signatures.
_PDF = b'%PDF-'
_ZIP = b'PK\x03\x04'                                   # docx (and any OOXML/zip)
_OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'             # legacy .doc/.xls
_RTF = b'{\\rtf'

# Executable / script signatures we refuse outright, whatever the extension.
_EXECUTABLE_MAGIC = (
    b'MZ',                      # Windows PE (.exe/.dll)
    b'\x7fELF',                 # Linux ELF
    b'\xfe\xed\xfa\xce',        # Mach-O 32
    b'\xfe\xed\xfa\xcf',        # Mach-O 64
    b'\xce\xfa\xed\xfe',        # Mach-O 32 (LE)
    b'\xcf\xfa\xed\xfe',        # Mach-O 64 (LE)
    b'\xca\xfe\xba\xbe',        # Mach-O fat / Java class
    b'#!',                      # shell script shebang
)

# Older Word / WordPad / CV builders emit RTF but save it as .doc, and the old
# filename-only check accepted those. Tightening the gate must not silently
# start rejecting real applicants, so .doc is a legal name for RTF content.
EXT_FOR_KIND = {'pdf': ('.pdf',), 'doc': ('.doc',), 'docx': ('.docx',),
                'rtf': ('.rtf', '.doc')}

# ── deep scan (Manus QC round 2026-08-27, HIGH) ─────────────────────────────
# The first-bytes check above stops an .exe posing as a CV, but NOT a real PDF
# with a Windows executable appended after it, nor a DOCX carrying macros or an
# embedded program. Those two got through. These constants + helpers close it.
#
# The PDF spec lets readers find "%PDF-" anywhere in the first 1024 bytes, so a
# legitimate PDF with a leading BOM or blank line is still valid — the old
# byte-0-only check wrongly rejected those (Manus MEDIUM). We look in a window.
PDF_HEADER_WINDOW = 1024

# A real Windows PE almost always carries this DOS-stub sentence, and a valid PE
# has the "PE\0\0" signature at the offset named by the 4 bytes at MZ+0x3C. Both
# are far more specific than a bare "MZ", which occurs by chance in binary
# streams inside legitimate PDFs (fonts, images) — checking them avoids false
# positives on clean documents.
_PE_DOS_STUB = b'This program cannot be run in DOS mode'
_PE_SIGNATURE = b'PE\x00\x00'
# ELF and Mach-O magics that, appearing anywhere past byte 0, mean an embedded
# native program (byte-0 hits are already caught by looks_executable()).
_EMBEDDED_NATIVE = (
    b'\x7fELF',
    b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf',
    b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe',
)

# OOXML parts that carry code / embedded programs — rejected for a CV upload
# (Manus: "reject OOXML embeddings, macros and active content").
_DANGEROUS_ZIP_EXTS = (
    '.exe', '.dll', '.scr', '.bat', '.cmd', '.com', '.pif', '.vbs', '.vbe',
    '.js', '.jse', '.wsf', '.wsh', '.jar', '.msi', '.ps1', '.hta', '.cpl',
)


def looks_executable(head: bytes) -> bool:
    """True if these first bytes are a program, not a document."""
    return any(head.startswith(sig) for sig in _EXECUTABLE_MAGIC)


def find_pdf_header(data: bytes) -> int:
    """Offset of the '%PDF-' marker within the first PDF_HEADER_WINDOW bytes,
    or -1. Tolerates a UTF-8 BOM / leading whitespace before the header, which
    a strict byte-0 check rejected on otherwise valid PDFs."""
    return data[:PDF_HEADER_WINDOW].find(_PDF)


def contains_embedded_executable(data: bytes) -> bool:
    """True if a native program is embedded *inside* an otherwise-valid document
    (e.g. a PE appended to a PDF). Byte-0 executables are handled separately by
    looks_executable(); this scans the whole body for a hidden payload."""
    if _PE_DOS_STUB in data:
        return True
    for sig in _EMBEDDED_NATIVE:
        if data.find(sig) > 0:
            return True
    # A DOS "MZ" header whose PE-header pointer lands on a real "PE\0\0" sig.
    mz = data.find(b'MZ')
    while mz != -1:
        if mz + 0x40 <= len(data):
            e_lfanew = int.from_bytes(data[mz + 0x3C:mz + 0x40], 'little')
            pe_at = mz + e_lfanew
            if 0 < e_lfanew and pe_at + 4 <= len(data) \
                    and data[pe_at:pe_at + 4] == _PE_SIGNATURE:
                return True
        mz = data.find(b'MZ', mz + 1)
    return False


def docx_active_content(fileobj) -> str | None:
    """A reason string if this OOXML zip carries macros, an embedded object, or
    an executable member; None if it is clean. Reads only names + each member's
    first bytes, so a zip bomb cannot cost us the member data."""
    try:
        fileobj.seek(0)
        with zipfile.ZipFile(fileobj) as z:
            names = z.namelist()
            for n in names:
                low = n.lower()
                base = low.rsplit('/', 1)[-1]
                if 'vbaproject' in base:
                    return 'macros'
                if '/embeddings/' in low or low.startswith('embeddings/'):
                    return 'an embedded object'
                if any(low.endswith(e) for e in _DANGEROUS_ZIP_EXTS):
                    return 'an executable attachment'
            for n in names:
                try:
                    with z.open(n) as m:
                        if looks_executable(m.read(HEAD_BYTES)):
                            return 'an embedded program'
                except Exception:      # noqa: BLE001 — unreadable member, skip
                    continue
    except Exception:      # noqa: BLE001 — a broken zip is handled by is_word_zip
        return None
    finally:
        try:
            fileobj.seek(0)
        except Exception:      # noqa: BLE001
            pass
    return None


def sniff_document(head: bytes) -> str | None:
    """Identify a document from its first bytes. None = not a known document.

    A ZIP header is reported as 'docx' provisionally — only is_word_zip() can
    tell a .docx from a .xlsx or a plain archive, and that needs the whole file.
    """
    if head.startswith(_PDF):
        return 'pdf'
    if head.startswith(_OLE):
        return 'doc'
    if head.startswith(_RTF):
        return 'rtf'
    if head.startswith(_ZIP):
        return 'docx'
    return None


def is_word_zip(fileobj) -> bool:
    """True if this zip is really a Word document.

    Reads the central directory only (cheap — not the member data), so a zip
    bomb costs us nothing here.
    """
    try:
        fileobj.seek(0)
        with zipfile.ZipFile(fileobj) as z:
            names = z.namelist()
        return any(n.startswith('word/') for n in names)
    except Exception:      # noqa: BLE001 — a broken zip is simply not a docx
        return False
    finally:
        try:
            fileobj.seek(0)
        except Exception:      # noqa: BLE001
            pass


def validate_document_upload(uploaded, *, allowed=('pdf', 'doc', 'docx', 'rtf'),
                             max_mb: int | None = None) -> str | None:
    """Validate an uploaded document by CONTENT. Returns an error string, or
    None when the file is acceptable.

    Checks, in order: size; byte-0 executable magic; real type (BOM-tolerant for
    PDF); an embedded native program hidden inside a PDF; macros / embedded
    objects / executables inside a DOCX; and finally that the real type agrees
    with the extension (so a .docx that is really a PDF is refused too — it would
    break the parser downstream).

    This is not full anti-virus (Omni runs no clamd today) — it cannot judge a
    genuine but weaponised PDF exploit. But it now rejects the wrong KIND of file
    AND the common smuggling tricks: an executable appended to a real PDF, and a
    macro-/embed-carrying DOCX (Manus QC 2026-08-27). The file is never executed
    by Omni and CVs are purged after six months, bounding the residual risk.
    """
    if uploaded is None:
        return 'Please attach a file.'

    if max_mb is not None and uploaded.size > max_mb * 1024 * 1024:
        return f'The file must be under {max_mb} MB.'

    # Read the whole file once (bounded), so we can scan the body — not just the
    # first bytes — for an appended/embedded payload. The size gate above already
    # caps it; with no explicit cap we still bound the scan to 25 MB.
    scan_cap = ((max_mb or 25) * 1024 * 1024) + 1
    try:
        uploaded.seek(0)
        data = uploaded.read(scan_cap)
    finally:
        try:
            uploaded.seek(0)
        except Exception:      # noqa: BLE001
            pass
    head = data[:HEAD_BYTES]

    if looks_executable(head):
        return 'That file is a program, not a document. Please upload a PDF or Word file.'

    kind = sniff_document(head)
    if kind is None and find_pdf_header(data) >= 0:
        kind = 'pdf'          # a valid PDF behind a BOM / leading whitespace
    if kind is None or kind not in allowed:
        return 'That file is not a PDF or Word document. Please upload a PDF or Word file.'

    # Scan the body of every NON-zip document type for an embedded/appended
    # program. Not just PDF: an OLE .doc (and RTF) is accepted by both CV
    # endpoints and stores embedded objects as raw bytes, so a PE hidden in a
    # .doc would otherwise skip this gate entirely (Fable review 2026-08-27).
    # docx is a zip and is covered by docx_active_content() below instead.
    if kind in ('pdf', 'doc', 'rtf') and contains_embedded_executable(data):
        return ('That file has a program hidden inside it and cannot be accepted. '
                'Please upload a clean PDF or Word file.')

    if kind == 'docx':
        buf = io.BytesIO(data)
        if not is_word_zip(buf):
            return 'That file is not a Word document. Please upload a PDF or Word file.'
        active = docx_active_content(buf)
        if active:
            return (f'That Word file contains {active} and cannot be accepted. '
                    'Please upload a plain PDF or Word file with no macros or embedded files.')

    name = (uploaded.name or '').lower()
    if not name.endswith(EXT_FOR_KIND[kind]):
        return (f'The file is really a {kind.upper()} but is named "{uploaded.name}". '
                'Please upload it with the correct file extension.')

    return None
