"""Plan-pack uploads must be stored decompressed.

Cloudflare's WAF rejects HTML/JS containing script tags, so Finance gzips those
files to get them through. The compressed bytes then land under the original
name and every download hands back a binary blob under an .html name. Two files
in the FY2026 Base Case pack were stored that way.
"""
import gzip
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from budgets.uploads import MAX_DECOMPRESSED_BYTES, looks_gzipped, normalise_upload


def gz(payload: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as f:
        f.write(payload)
    return buf.getvalue()


def upload(name: str, data: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data)


class LooksGzippedTests(SimpleTestCase):
    def test_detects_the_gzip_magic(self):
        self.assertTrue(looks_gzipped(b'\x1f\x8b\x08\x00'))

    def test_plain_html_is_not_gzip(self):
        self.assertFalse(looks_gzipped(b'<!DO'))

    def test_empty_input_is_not_gzip(self):
        self.assertFalse(looks_gzipped(b''))


class NormaliseUploadTests(SimpleTestCase):
    def test_gzipped_html_under_an_html_name_is_decompressed(self):
        """The exact shape of the two broken rows in the Base Case pack."""
        html = b'<!DOCTYPE html><html><body><script>x=1</script></body></html>'
        f, note = normalise_upload(upload('AD_Insurtech_5YearPlanCockpit.html', gz(html)))

        self.assertEqual(f.read(), html)
        self.assertEqual(f.name, 'AD_Insurtech_5YearPlanCockpit.html')
        self.assertIn('decompressed', note)

    def test_a_gz_suffix_is_stripped(self):
        js = b'const a = 1;\n'
        f, note = normalise_upload(upload('build_library_spec.js.gz', gz(js)))

        self.assertEqual(f.read(), js)
        self.assertEqual(f.name, 'build_library_spec.js')
        self.assertIn('decompressed', note)

    def test_a_plain_file_is_passed_through_untouched(self):
        html = b'<!DOCTYPE html><html></html>'
        original = upload('library.html', html)
        f, note = normalise_upload(original)

        self.assertIs(f, original)
        self.assertEqual(note, '')
        self.assertEqual(f.read(), html)

    def test_a_docx_is_not_mistaken_for_gzip(self):
        """xlsx/docx are PK zips — different magic, must not be touched."""
        f, note = normalise_upload(upload('spec.docx', b'PK\x03\x04rest-of-zip'))
        self.assertEqual(note, '')
        self.assertEqual(f.read(), b'PK\x03\x04rest-of-zip')

    def test_a_pdf_is_untouched(self):
        f, note = normalise_upload(upload('spec.pdf', b'%PDF-1.7\nbody'))
        self.assertEqual(note, '')
        self.assertEqual(f.read(), b'%PDF-1.7\nbody')

    def test_a_genuine_targz_archive_is_kept_compressed(self):
        blob = gz(b'tar-ish content')
        f, note = normalise_upload(upload('bundle.tar.gz', blob))

        self.assertEqual(note, '')
        self.assertEqual(f.read(), blob, 'a real archive must stay an archive')

    def test_truncated_gzip_keeps_the_original_bytes(self):
        """Never silently lose the upload because inflate failed."""
        broken = gz(b'x' * 500)[:12]
        f, note = normalise_upload(upload('cockpit.html', broken))

        self.assertEqual(note, '')
        self.assertEqual(f.read(), broken)

    def test_gzip_magic_over_random_bytes_keeps_the_original(self):
        junk = b'\x1f\x8bnot actually gzip at all'
        f, note = normalise_upload(upload('thing.html', junk))

        self.assertEqual(note, '')
        self.assertEqual(f.read(), junk)

    def test_a_decompression_bomb_is_refused(self):
        """A few KB can inflate to gigabytes; this runs before anything else
        reads the file, so it must not be allowed to."""
        bomb = gz(b'\0' * (MAX_DECOMPRESSED_BYTES + 1024))
        f, note = normalise_upload(upload('bomb.html', bomb))

        self.assertEqual(note, '', 'must refuse rather than inflate')
        self.assertEqual(f.read(), bomb, 'original bytes kept')

    def test_the_file_is_rewound_for_the_caller(self):
        html = b'<!DOCTYPE html><p>hi</p>'
        f, _ = normalise_upload(upload('x.html', gz(html)))
        self.assertEqual(f.read(), html, 'read from position 0')

    def test_empty_gzip_member_keeps_the_original(self):
        blob = gz(b'')
        f, note = normalise_upload(upload('empty.html', blob))
        self.assertEqual(note, '')
        self.assertEqual(f.read(), blob)
