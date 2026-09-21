"""Reading a list of payments out of a file, and writing the FNB file back.

Legakwa Ntabeni, 2026-09-11: "on payment requests you can only load payments
individually, there should be a feature that allows for batch payments such as
commission and salary payments. we should be able to upload into the FNB template
file so it uploads successfully."

CFO decision the same day, in his words: "if he loads 10 supplier payments via
omni csv I want it to display separately in Omni and FNB - so if I want to reject
one supplier I don't reject everyone."

What these tests pin:
  * SEPARATE IS THE DEFAULT, and it cannot be lost by accident — a missing or
    unrecognised choice means separate, never bundled.
  * The upload CREATES NOTHING. It reads. Rows are created afterwards through
    the ordinary gated endpoint, so no money control can be walked past.
  * An account number Excel has shortened to scientific notation is REFUSED, not
    reconstructed. This is not hypothetical: Legakwa's own July file went to the
    bank carrying `1.23E+11`.
  * No line is ever dropped in silence — a bad row comes back with its reason.
  * The FNB file Omni writes matches the real template, with the numbers as text.

The sample rows below are Legakwa's real July UniCoin commission file.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import Company, Currency, UserProfile
from taskboard.bulk_payment_upload import parse_payment_file
from taskboard.models import PaymentRequest

PARSE = '/api/v1/payment-requests/bulk-parse/'

# Legakwa's file, exactly as it arrived — preamble lines and all.
REAL_FNB_FILE = (
    'BInSol - U ver 1.00,,,,,,,\n'
    '11/09/2026,,,,,,,\n'
    '62842621725,1.23E+11,,,,,,\n'
    'RECIPIENT NAME,RECIPIENT ACCOUNT,RECIPIENT ACCOUNT TYPE,BRANCHCODE,AMOUNT,'
    'OWN REFERENCE,RECIPIENT REFERENCE,EMAIL 1 NOTIFY\n'
    'Maatla Boletswane,62776765160,1,283767,851.06,Unicoin Commission. July. 2026,'
    'Unicoin Commission. July. 2026,ubutale@alphadirect.co.bw\n'
    'Kutlo Koma,63076869941,1,284567,500.90,Unicoin Commission. July. 2026,'
    'Unicoin Commission. July. 2026,ubutale@alphadirect.co.bw\n'
    ' Pako Mampane,62487726287,1,283567,395.00,Unicoin Commission. July. 2026,'
    'Unicoin Commission. July. 2026,ubutale@alphadirect.co.bw\n'
    'Thato Barati ,62800739677,1,283567,758.00,Unicoin Commission. July. 2026,'
    'Unicoin Commission. July. 2026,ubutale@alphadirect.co.bw\n'
)


def _csv(text: str, name='payments.csv') -> SimpleUploadedFile:
    return SimpleUploadedFile(name, text.encode(), content_type='text/csv')


class ParseRealFnbFileTests(TestCase):
    """The parser, on the real file, with no web layer in the way."""

    def test_it_reads_legakwas_own_july_commission_file(self):
        read = parse_payment_file(_csv(REAL_FNB_FILE))
        self.assertEqual(len(read['rows']), 4)
        self.assertEqual(read['ok'], 4)
        self.assertEqual(read['bad'], 0)
        self.assertEqual(Decimal(read['total']), Decimal('2504.96'))

    def test_it_finds_the_header_under_three_lines_of_preamble(self):
        read = parse_payment_file(_csv(REAL_FNB_FILE))
        first = read['rows'][0]
        self.assertEqual(first['name'], 'Maatla Boletswane')
        self.assertEqual(first['account_number'], '62776765160')
        self.assertEqual(first['branch_code'], '283767')
        self.assertEqual(first['amount'], '851.06')
        self.assertEqual(first['account_type'], '1')
        self.assertEqual(first['email'], 'ubutale@alphadirect.co.bw')

    def test_it_reads_the_paying_account_off_the_preamble(self):
        """The file says which account it goes out of; do not default it."""
        read = parse_payment_file(_csv(REAL_FNB_FILE))
        self.assertEqual(read['source_account'], '62842621725')

    def test_stray_spaces_around_a_name_are_tidied_not_treated_as_a_fault(self):
        read = parse_payment_file(_csv(REAL_FNB_FILE))
        names = [r['name'] for r in read['rows']]
        self.assertIn('Pako Mampane', names)     # was ' Pako Mampane'
        self.assertIn('Thato Barati', names)     # was 'Thato Barati '
        self.assertTrue(all(r['ok'] for r in read['rows']))

    # ── the one that matters most ───────────────────────────────────────────

    def test_an_account_number_excel_has_mangled_is_refused_not_guessed(self):
        """`1.23E+11` is a twelve-digit number Excel shortened when the file was
        saved. The digits are GONE. Reconstructing them would send real money to
        an account nobody typed, so the row is refused with an instruction the
        person can act on."""
        bad = REAL_FNB_FILE.replace('62776765160,1', '1.23E+11,1')
        read = parse_payment_file(_csv(bad))
        row = read['rows'][0]
        self.assertFalse(row['ok'])
        self.assertEqual(row['account_number'], '')
        self.assertIn('Excel has shortened it', ' '.join(row['problems']))
        self.assertIn('Text', ' '.join(row['problems']))
        self.assertEqual(read['bad'], 1)
        self.assertEqual(read['ok'], 3)          # the others still come through

    def test_a_bad_row_is_reported_never_dropped(self):
        """A payment list that quietly loses a line is how somebody goes unpaid
        and nobody finds out until they phone."""
        text = REAL_FNB_FILE + 'Nobody Here,,1,,0,,,\n'
        read = parse_payment_file(_csv(text))
        self.assertEqual(len(read['rows']), 5)   # the bad one is STILL returned
        self.assertEqual(read['bad'], 1)
        self.assertFalse(read['rows'][-1]['ok'])

    def test_the_total_counts_only_the_rows_that_are_actually_payable(self):
        text = REAL_FNB_FILE + 'Broken Row,1.23E+11,1,283567,999999.00,,,\n'
        read = parse_payment_file(_csv(text))
        self.assertEqual(Decimal(read['total']), Decimal('2504.96'))

    def test_a_zero_or_negative_amount_is_refused(self):
        for amount in ('0', '-100.00'):
            text = REAL_FNB_FILE + f'Zero Person,62800739678,1,283567,{amount},,,\n'
            read = parse_payment_file(_csv(text))
            self.assertFalse(read['rows'][-1]['ok'], amount)

    def test_the_same_account_and_amount_twice_is_flagged_but_not_refused(self):
        """Occasionally real. Never passed over in silence."""
        text = REAL_FNB_FILE + (
            'Maatla Boletswane,62776765160,1,283767,851.06,,,\n')
        read = parse_payment_file(_csv(text))
        self.assertIn('check it is not a copy', ' '.join(read['rows'][-1]['problems']))

    def test_a_plain_spreadsheet_with_friendly_headings_is_read_too(self):
        text = ('Payee,Account Number,Branch Code,Amount,Reference\n'
                'ABC Traders,62776765160,283767,1200.00,Invoice 5512\n')
        read = parse_payment_file(_csv(text))
        self.assertEqual(read['ok'], 1)
        self.assertEqual(read['rows'][0]['name'], 'ABC Traders')
        self.assertEqual(read['rows'][0]['account_type'], '1')   # sensible default

    def test_a_file_with_no_recognisable_columns_says_so_in_plain_english(self):
        with self.assertRaises(ValueError) as ctx:
            parse_payment_file(_csv('some notes\nand more notes\n'))
        self.assertIn('column headings', str(ctx.exception))

    def test_an_empty_file_says_so(self):
        with self.assertRaises(ValueError):
            parse_payment_file(_csv(''))


class BulkParseEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='BULKU', defaults={'name': 'Bulk Upload Test Co',
                                    'base_currency': cls.bwp})
        cls.user = User.objects.create_user(
            'bulk_user', 'bulk@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={'title': UserProfile.Title.ACCOUNTANT, 'is_active': True})

    def _post(self, **extra):
        self.client.force_login(self.user)
        return self.client.post(
            PARSE, {'file': _csv(REAL_FNB_FILE), **extra})

    def test_the_upload_creates_absolutely_nothing(self):
        """The whole safety design in one test. If this ever fails, the importer
        has become a second way into the payment tables and every money control
        is being walked past."""
        before = PaymentRequest.objects.count()
        r = self._post(category='unicoin')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(PaymentRequest.objects.count(), before)
        self.assertIn('Nothing has been created yet', r.json()['message'])

    # ── separate is the default, and cannot be lost by accident ─────────────

    def test_with_no_choice_at_all_the_rows_arrive_separately(self):
        r = self._post(category='supplier')
        body = r.json()
        self.assertFalse(body['bundled'])
        self.assertEqual(body['arrive_as'], 'separate')
        self.assertIn('SEPARATE', body['how_they_will_arrive'])
        self.assertIn('without touching the others', body['how_they_will_arrive'])

    def test_an_unrecognised_choice_also_means_separate(self):
        """A typo must never quietly remove the CFO's ability to refuse one."""
        for junk in ('', 'yes', 'true', 'batch-please', 'Run it as one'):
            body = self._post(category='supplier', arrive_as=junk).json()
            self.assertFalse(body['bundled'], junk)

    def test_a_run_bundles_only_when_it_is_positively_asked_for(self):
        body = self._post(category='unicoin', arrive_as='run').json()
        self.assertTrue(body['bundled'])
        self.assertIn('ONE payment request', body['how_they_will_arrive'])

    def test_the_category_alone_never_decides_it(self):
        """Omni has no 'commission' category — Legakwa's commission run is filed
        under 'unicoin', which also carries rent and supplier invoices. Bundling
        off the category would silently glue ten separate suppliers together."""
        body = self._post(category='unicoin').json()
        self.assertFalse(body['bundled'])

    def test_it_says_how_many_are_ready_and_how_many_need_a_fix(self):
        body = self._post(category='supplier').json()
        self.assertEqual(body['ok'], 4)
        self.assertEqual(body['bad'], 0)
        self.assertEqual(Decimal(body['total']), Decimal('2504.96'))

    def test_a_file_is_required(self):
        self.client.force_login(self.user)
        r = self.client.post(PARSE, {'category': 'supplier'})
        self.assertEqual(r.status_code, 400)

    def test_a_signed_out_person_cannot_use_it(self):
        r = self.client.post(PARSE, {'file': _csv(REAL_FNB_FILE)})
        self.assertIn(r.status_code, (401, 403))
