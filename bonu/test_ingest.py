"""
bonu/test_ingest.py — the invoice reader, tested where it actually breaks.

The first version of the money pattern required a thousands separator or decimals, so
`26562` — exactly what an Excel cell hands over — matched nothing and every uploaded
workbook came back with no total. That is the kind of miss that quietly turns automation
into re-typing, so it gets a test per shape.

No database is touched: SimpleTestCase, because reading a document is pure text work.
"""
from django.test import SimpleTestCase

from bonu.ingest import extract_text, guess_header


class MoneyShapesTests(SimpleTestCase):
    """Every way a money figure appears on a Botswana legal invoice."""

    def test_reads_thousands_separator(self):
        self.assertEqual(guess_header('TOTAL DUE 26,562.00')['total'], 26562.00)

    def test_reads_space_separator(self):
        self.assertEqual(guess_header('TOTAL DUE 26 562')['total'], 26562.00)

    def test_reads_plain_decimal(self):
        self.assertEqual(guess_header('TOTAL DUE 26562.00')['total'], 26562.00)

    def test_reads_bare_integer_from_a_spreadsheet_cell(self):
        # The regression: openpyxl gives 26562, not "26,562.00".
        self.assertEqual(guess_header('TOTAL DUE 26562')['total'], 26562.00)

    def test_largest_figure_wins_as_the_total(self):
        text = 'Fees 4500\nDisbursements 1500\nTOTAL 6000'
        self.assertEqual(guess_header(text)['total'], 6000.00)

    def test_hours_and_small_counts_are_not_money(self):
        # 3 hours at 1500 = 4500. Only the money figures may be picked up as a total.
        self.assertEqual(guess_header('Consultation 3 hours')['total'], None)


class HeaderTests(SimpleTestCase):

    def test_reads_invoice_number_and_iso_date(self):
        hdr = guess_header('TAX INVOICE No: JT-2026-114\nDate: 2026-07-31')
        self.assertEqual(hdr['invoice_number'], 'JT-2026-114')
        self.assertEqual(hdr['invoice_date'], '2026-07-31')

    def test_reads_a_local_day_month_year_date(self):
        self.assertEqual(guess_header('Invoice INV/9 dated 31/07/2026')['invoice_date'],
                         '2026-07-31')

    def test_nothing_found_returns_blanks_not_a_guess(self):
        hdr = guess_header('Dear Sir, please find attached.')
        self.assertEqual(hdr['invoice_number'], '')
        self.assertEqual(hdr['invoice_date'], '')
        self.assertIsNone(hdr['total'])


class ExtractionLadderTests(SimpleTestCase):
    """The ladder must report what it cannot read, never invent a figure."""

    def test_excel_is_read_exactly_with_no_model(self):
        import io

        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['TAX INVOICE No: JT-2026-114'])
        ws.append(['Date', '2026-07-31'])
        ws.append(['Attendance on matter 118/2026', 3, 1500, 4500])
        buf = io.BytesIO()
        wb.save(buf)

        text, method, err = extract_text('bill.xlsx', buf.getvalue())
        self.assertEqual(err, '')
        self.assertIn('openpyxl', method)
        self.assertIn('118/2026', text)
        hdr = guess_header(text)
        self.assertEqual(hdr['invoice_number'], 'JT-2026-114')
        self.assertEqual(hdr['invoice_date'], '2026-07-31')
        # This sheet has NO line labelled as a total — just one charge row. It
        # used to return 4,500 because that was the largest figure present, and
        # on this particular bill that happened to be right. It is luck, not a
        # rule: add the firm's bank account number to the footer, as a real bill
        # has, and the same logic returns sixty-two billion (see
        # bonu/test_ingest_total.py for the eight layouts that proved it).
        #
        # So an unlabelled document now reports NOTHING and the person types the
        # amount. The figure gates a spend cap that REFUSES a bill, and a
        # pre-filled number gets confirmed unread — a blank is honest, a
        # plausible wrong number is not. The reference and date still read, so
        # the reader still saves most of the typing. (CFO + Fable, 9 Sep 2026.)
        self.assertIsNone(hdr['total'])

    def test_csv_is_read_exactly(self):
        blob = b'Invoice No: JT-2026-115\nmatter,units,rate,amount\n118/2026,2,1500,3000\n'
        text, method, err = extract_text('bill.csv', blob)
        self.assertEqual(err, '')
        self.assertIn('csv', method)
        self.assertEqual(guess_header(text)['invoice_number'], 'JT-2026-115')

    def test_an_image_is_reported_not_guessed(self):
        _, method, err = extract_text('scan.jpg', b'\xff\xd8\xff')
        self.assertEqual(method, 'image')
        self.assertIn('vision model', err)

    def test_an_unreadable_pdf_is_reported_not_guessed(self):
        _, method, err = extract_text('bill.pdf', b'not really a pdf')
        self.assertEqual(method, 'pdf')
        self.assertTrue(err)

    def test_an_unsupported_type_is_named(self):
        _, method, err = extract_text('bill.docx', b'PK\x03\x04')
        self.assertEqual(method, 'unknown')
        self.assertIn('docx', err)
