"""Feature requests may carry a document, not only screenshots.

Oratile Tlhomelang asked for this on the "Ask for a New Feature" page on
2026-09-08: her requests are already written up in Excel, Word or PDF (a build
brief, a framework, a policy extract), and the page only accepted images. People
work around that by emailing the CFO the document separately — which is exactly
what this channel exists to stop.

Two rules are being locked in here:
  * a document (PDF / Word / Excel / CSV) is an acceptable attachment; and
  * a document does NOT count as a screenshot — a BUG still needs its two real
    images, otherwise "attach two screenshots" could be satisfied with a
    spreadsheet and the triager gets nothing to look at.
"""
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient, APITestCase

from core.models import BugReport

PNG = (b'\x89PNG\r\n\x1a\n' + b'0' * 64)
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _image(name='shot.png'):
    return SimpleUploadedFile(name, PNG, content_type='image/png')


def _words(n):
    return ' '.join(['word'] * n)


class BugReportAttachmentTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'oratile', email='otlhomelang@alphadirect.co.bw', password='x')
        self.c = APIClient()
        self.c.force_authenticate(user=self.user)

    def _post(self, description, files):
        return self.c.post(
            '/api/v1/bug-reports/',
            {'description': description, 'page_url': '/report-bug', 'screenshots': files},
            format='multipart')

    def test_feature_request_accepts_a_word_document_with_no_screenshot(self):
        r = self._post(
            '[FEATURE REQUEST] ' + _words(40),
            [SimpleUploadedFile('build-brief.docx', b'PK\x03\x04brief', content_type=DOCX_MIME)])
        self.assertEqual(r.status_code, 201, r.content)
        report = BugReport.objects.get(id=r.json()['id'])
        # The document rode the email; it is not a screenshot, so the count is 0.
        self.assertEqual(report.screenshot_count, 0)

    def test_feature_request_accepts_a_spreadsheet_and_a_pdf(self):
        r = self._post('[FEATURE REQUEST] ' + _words(40), [
            SimpleUploadedFile('register.xlsx', b'PK\x03\x04sheet', content_type=XLSX_MIME),
            SimpleUploadedFile('dpa.pdf', b'%PDF-1.7', content_type='application/pdf'),
        ])
        self.assertEqual(r.status_code, 201, r.content)

    def test_a_bug_still_needs_two_real_screenshots(self):
        # One image plus a spreadsheet is NOT two screenshots.
        r = self._post(_words(60), [
            _image(),
            SimpleUploadedFile('numbers.xlsx', b'PK\x03\x04sheet', content_type=XLSX_MIME),
        ])
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('at least 2 screenshots', r.json()['errors']['screenshots'])

    def test_a_bug_with_two_screenshots_and_a_document_is_accepted(self):
        r = self._post(_words(60), [
            _image('one.png'), _image('two.png'),
            SimpleUploadedFile('workings.xlsx', b'PK\x03\x04sheet', content_type=XLSX_MIME),
        ])
        self.assertEqual(r.status_code, 201, r.content)
        report = BugReport.objects.get(id=r.json()['id'])
        self.assertEqual(report.screenshot_count, 2)

    def test_an_executable_is_still_refused(self):
        r = self._post('[FEATURE REQUEST] ' + _words(40), [
            SimpleUploadedFile('installer.exe', b'MZ', content_type='application/x-msdownload'),
        ])
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('installer.exe', r.json()['errors']['screenshots'])

    def test_the_email_names_the_documents_so_the_reader_looks_past_the_images(self):
        from core.bug_report_views import BugReportView
        html = BugReportView._build_html(
            report_id='abc', reporter_name='Test Reporter', reporter_email='t@alphadirect.co.bw',
            page_url='/report-bug', word_count=40, shot_count=0,
            description='a request', doc_names=['build-brief.docx', 'register.xlsx'])
        self.assertIn('build-brief.docx', html)
        self.assertIn('register.xlsx', html)
