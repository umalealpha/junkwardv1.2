"""
Downloading a petty cash receipt — Keetile, 5 August 2026: "when i try to download the attached
receipt, i get a server error."

The serializer had been handing out `file.url`, i.e. `/media/petty-cash-receipts/...`. Django does
not serve /media/ in production and the proxy has no route for it, so **every** receipt download
returned 404. Confirmed against the real file on production: it existed on disk (1.7 MB) and its
URL returned 404 both through the public site and straight at the backend.

Serving it through the API fixes the download and closes a hole in the same move: a media path
carries no permission check, so anyone with the link could have read a till slip or an invoice.
The DPA register already tracked this pattern as open risk H-7, "Latent raw file.url in some
APIs" — it stopped being latent today.
"""
import datetime as dt
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from petty_cash.api_views import PettyCashVoucherViewSet
from petty_cash.models import PettyCashVoucherReceipt
from petty_cash.serializers import PettyCashVoucherReceiptSerializer

User = get_user_model()
# An apostrophe on purpose: the file that surfaced this was "Mr A. Iyer- Coke Zero's.jpg", and a
# quote in a filename is exactly what breaks a hand-rolled Content-Disposition header.
AWKWARD_NAME = "Mr A. Iyer- Coke Zero's.jpg"


# Uploaded files go to a throwaway directory: the source tree is mounted read-only in CI, and a
# test has no business writing into the repo either way.
MEDIA = tempfile.mkdtemp(prefix='petty-receipt-test-')


# These tests deliberately drive a request from the real production host, so the
# asserted download URL is the one a user would actually be handed. Django checks
# the host against ALLOWED_HOSTS, which in CI is only localhost, so
# build_absolute_uri raised DisallowedHost and the test errored — correct security
# behaviour meeting a test that asked for a host it had not allowed. Allow it
# here; do NOT soften the check in the serializer.
@override_settings(MEDIA_ROOT=MEDIA,
                   ALLOWED_HOSTS=['omni.alphadirect.co.bw', 'testserver', 'localhost'])
class ReceiptDownloadTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user('petty-dl-test', password='x',
                                            is_superuser=True, is_staff=True)
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        self.voucher = self._voucher()
        self.receipt = PettyCashVoucherReceipt.objects.create(
            voucher=self.voucher,
            file=SimpleUploadedFile(AWKWARD_NAME, b'\xff\xd8\xff till slip bytes',
                                    content_type='image/jpeg'),
            filename=AWKWARD_NAME, file_size_bytes=19, content_type='image/jpeg',
            uploaded_by=self.user)

    def _voucher(self):
        """The smallest voucher the model will accept."""
        from core.models import Company
        from ledger.models import Account, Currency
        from petty_cash.models import PettyCashLocation, PettyCashVoucher
        bwp, _ = Currency.objects.get_or_create(code='BWP',
                                                defaults={'name': 'Pula', 'symbol': 'P'})
        company, _ = Company.objects.get_or_create(
            code='PCDL', defaults={'name': 'Petty Cash DL Test', 'base_currency': bwp})
        expense, _ = Account.objects.get_or_create(
            code='PC-EXP-DL', defaults={'name': 'Refreshments', 'account_type': 'expense',
                                        'currency_code': bwp})
        float_acc, _ = Account.objects.get_or_create(
            code='PC-FLOAT-DL', defaults={'name': 'Petty cash float', 'account_type': 'asset',
                                          'currency_code': bwp})
        bank, _ = Account.objects.get_or_create(
            code='PC-BANK-DL', defaults={'name': 'Reimbursing bank', 'account_type': 'asset',
                                         'currency_code': bwp, 'is_bank_account': True})
        # A unique name per call: one test needs a SECOND voucher, and location names are unique.
        ReceiptDownloadTests._seq = getattr(ReceiptDownloadTests, '_seq', 0) + 1
        loc = PettyCashLocation.objects.create(
            company=company, name=f'Head Office DL {ReceiptDownloadTests._seq}',
            float_amount=Decimal('5000.00'),
            petty_cash_account=float_acc, reimbursing_bank_account=bank)
        return PettyCashVoucher.objects.create(
            location=loc, voucher_date=dt.date(2026, 8, 5), payee='Shop',
            amount=Decimal('25.00'), expense_account=expense,
            description='Cash for refreshments')

    def _download(self, receipt_id=None):
        req = self.rf.get('/x/')
        force_authenticate(req, user=self.user)
        view = PettyCashVoucherViewSet.as_view({'get': 'download_receipt'})
        return view(req, pk=str(self.voucher.pk),
                    receipt_id=str(receipt_id or self.receipt.pk))

    # ── the reported bug ────────────────────────────────────────────────────
    def test_the_download_url_is_the_api_route_not_a_media_path(self):
        req = self.rf.get('/x/')
        data = PettyCashVoucherReceiptSerializer(self.receipt, context={'request': req}).data
        url = data['download_url']
        self.assertNotIn('/media/', url, 'a raw media path is not served and is not permissioned')
        self.assertIn(f'/api/v1/petty-cash-vouchers/{self.voucher.pk}/receipts/', url)
        self.assertTrue(url.endswith('/download/'))

    def test_the_receipt_actually_downloads(self):
        resp = self._download()
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment', resp.headers.get('Content-Disposition', ''))

    def test_the_bytes_that_come_back_are_the_bytes_that_went_in(self):
        resp = self._download()
        body = b''.join(resp.streaming_content) if resp.streaming else resp.content
        self.assertEqual(body, b'\xff\xd8\xff till slip bytes')

    def test_an_apostrophe_in_the_filename_does_not_break_the_header(self):
        # The real file was "Mr A. Iyer- Coke Zero's.jpg".
        resp = self._download()
        self.assertEqual(resp.status_code, 200)
        cd = resp.headers.get('Content-Disposition', '')
        self.assertTrue('Coke' in cd or 'filename' in cd, cd)

    # ── failure paths must be honest, not 500s ──────────────────────────────
    def test_an_unknown_receipt_is_a_404_not_a_crash(self):
        resp = self._download(receipt_id='0' * 8 + '-0000-0000-0000-' + '0' * 12)
        self.assertEqual(resp.status_code, 404)

    def test_a_missing_file_on_disk_says_so_instead_of_500ing(self):
        # The row survives, the bytes do not — a restore gone wrong, say.
        self.receipt.file.storage.delete(self.receipt.file.name)
        resp = self._download()
        self.assertEqual(resp.status_code, 404)
        self.assertIn('missing', str(resp.data).lower())

    def test_a_receipt_from_another_voucher_cannot_be_fetched_through_this_one(self):
        other = self._voucher()
        stolen = PettyCashVoucherReceipt.objects.create(
            voucher=other,
            file=SimpleUploadedFile('other.jpg', b'not yours', content_type='image/jpeg'),
            filename='other.jpg', file_size_bytes=9, content_type='image/jpeg',
            uploaded_by=self.user)
        resp = self._download(receipt_id=stolen.pk)
        self.assertEqual(resp.status_code, 404)

    def test_an_anonymous_caller_gets_nothing(self):
        req = self.rf.get('/x/')          # no force_authenticate
        view = PettyCashVoucherViewSet.as_view({'get': 'download_receipt'})
        resp = view(req, pk=str(self.voucher.pk), receipt_id=str(self.receipt.pk))
        self.assertIn(resp.status_code, (401, 403))
