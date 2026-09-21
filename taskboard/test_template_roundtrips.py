"""The blank template must survive the reader that consumes it.

The "Download a blank template" button on the bulk upload screen shipped with
the header `Payee name` and account types `cheque` / `savings`. All three read
perfectly well to a person and all three are REFUSED by the parser: the column
matcher squashes `Payee name` to `payeename`, which is not an alias for the
name column, and the account type has to be one of FNB's codes 1-4. So the one
file we hand people to start from was a file we then rejected. (/fabe
2026-09-13.)

This test reads the template out of the screen's own source, so it cannot drift
away from what the button actually produces.
"""
import io
import pathlib
import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from taskboard.bulk_payment_upload import parse_payment_file

TSX = (pathlib.Path(__file__).resolve().parent.parent
       / 'frontend' / 'src' / 'components' / 'payments' / 'BulkPaymentUpload.tsx')


def _template_csv() -> str:
    """Pull the CSV lines out of downloadTemplate() in the screen's source."""
    src = io.open(TSX, encoding='utf-8').read()
    body = src.split('const downloadTemplate', 1)[1].split('].join', 1)[0]
    lines = re.findall(r"^\s*'(.*)',\s*$", body, flags=re.M)
    assert lines, 'could not find the template rows in downloadTemplate()'
    return '\r\n'.join(lines)


class TheBlankTemplateIsReadableByOurOwnParser(SimpleTestCase):

    def test_every_row_of_the_template_parses_with_no_problems(self):
        csv = _template_csv()
        res = parse_payment_file(SimpleUploadedFile(
            'omni-payment-upload-template.csv', csv.encode('utf-8'),
            content_type='text/csv'))
        self.assertEqual(
            res['bad'], 0,
            'the template we hand people is refused by our own reader: '
            + '; '.join(f"{r.get('name')}: {r.get('problems')}"
                        for r in res['rows'] if r.get('problems')))
        self.assertEqual(res['ok'], 2, res['rows'])
        self.assertEqual(res['rows'][0]['name'], 'Example Supplier (Pty) Ltd')
