"""payments/test_invoice_read.py — the invoice reader (CFO 2026-08-22).

The reader must: use the own-OCR/text ladder first, fall back to the vision model
only for a scan with no text, clean the extracted values to the shapes the form
and the bank expect, and refuse to pre-fill when it found neither money nor an
account. No DB needed — the OCR + AI dependencies are mocked.
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase


class _Parsed:
    def __init__(self, text):
        self.text = text


class InvoiceReadTests(SimpleTestCase):

    def _run(self, *, text='', reasoning=None, vision=None, mime='', filename=''):
        with mock.patch('core.doc_parse.parse', return_value=_Parsed(text)), \
             mock.patch('core.ai_assist.reasoning_complete',
                        return_value=reasoning) as rc, \
             mock.patch('core.ai_assist.vision_complete',
                        return_value=vision) as vc:
            from payments.invoice_read import read_invoice
            out = read_invoice(b'dummy', mime=mime, filename=filename)
        return out, rc, vc

    def test_reads_a_born_digital_invoice_via_the_text_ladder(self):
        out, _, vc = self._run(
            text='ABC Traders  INV-77  Total Due 12,345.60  A/C 001 234 567  FNB',
            reasoning='{"total_amount":"12,345.60","account_number":"001 234 567",'
                      '"bank_name":"FNB","branch_code":"28-21-72",'
                      '"invoice_number":"INV-77","payee_name":"ABC Traders"}',
            mime='application/pdf', filename='inv.pdf')
        self.assertTrue(out['ok'], msg=str(out))
        self.assertEqual(out['tier'], 'text')
        # Cleaned: commas gone + 2dp; account/branch digits only.
        self.assertEqual(out['fields']['total_amount'], '12345.60')
        self.assertEqual(out['fields']['account_number'], '001234567')
        self.assertEqual(out['fields']['branch_code'], '282172')
        self.assertEqual(out['fields']['payee_name'], 'ABC Traders')
        vc.assert_not_called()      # text was enough — no external image read

    def test_falls_back_to_vision_for_a_scan_with_no_text(self):
        out, _, vc = self._run(
            text='',                # own OCR yielded nothing
            vision='{"total_amount":"500","account_number":"62999999999",'
                   '"bank_name":null,"branch_code":null,"invoice_number":null,'
                   '"payee_name":"Grand RE"}',
            mime='image/jpeg', filename='scan.jpg')
        self.assertTrue(out['ok'], msg=str(out))
        self.assertEqual(out['tier'], 'vision')
        self.assertEqual(out['fields']['total_amount'], '500.00')
        self.assertEqual(out['fields']['account_number'], '62999999999')
        vc.assert_called_once()

    def test_a_text_pdf_never_calls_the_external_vision_model(self):
        """A born-digital PDF that yields useful fields must not fall through to the
        image path — that path sends the raw document to an outside model."""
        _, _, vc = self._run(
            text='Total due 900 A/C 12345678',
            reasoning='{"total_amount":"900","account_number":"12345678",'
                      '"bank_name":null,"branch_code":null,"invoice_number":null,'
                      '"payee_name":null}',
            mime='application/pdf', filename='inv.pdf')
        vc.assert_not_called()

    def test_unreadable_invoice_returns_ok_false_and_fills_nothing(self):
        out, _, _ = self._run(
            text='thank you for your business',
            reasoning='{"total_amount":null,"account_number":null,"bank_name":null,'
                      '"branch_code":null,"invoice_number":null,"payee_name":"X"}',
            mime='application/pdf', filename='x.pdf')
        self.assertFalse(out['ok'])
        self.assertIsNone(out['fields']['total_amount'])
        self.assertIn('type the details', out['message'].lower())

    def test_a_scanned_pdf_reaches_the_vision_model(self):
        """A scanner emits a PDF with no text layer — core.doc_parse returns
        nothing. Without page-1 rasterisation the vision path never fires and the
        commonest real scan dead-ends. Red without the _pdf_first_page_png hop."""
        with mock.patch('payments.invoice_read._pdf_first_page_png',
                        return_value=b'fake-png-bytes') as raster, \
             mock.patch('core.doc_parse.parse', return_value=_Parsed('')), \
             mock.patch('core.ai_assist.reasoning_complete', return_value=None), \
             mock.patch('core.ai_assist.vision_complete',
                        return_value='{"total_amount":"742.50","account_number":'
                        '"62012345678","bank_name":"FNB","branch_code":null,'
                        '"invoice_number":"INV-9","payee_name":"Grand RE"}') as vc:
            from payments.invoice_read import read_invoice
            out = read_invoice(b'%PDF-scan', mime='application/pdf',
                               filename='scan.pdf')
        raster.assert_called_once()
        vc.assert_called_once()
        self.assertTrue(out['ok'], msg=str(out))
        self.assertEqual(out['tier'], 'vision')
        self.assertEqual(out['fields']['total_amount'], '742.50')
        self.assertEqual(out['fields']['account_number'], '62012345678')

    def test_a_scan_the_vision_model_also_cannot_read_returns_ok_false(self):
        out, _, vc = self._run(
            text='', vision='{"total_amount":null,"account_number":null,'
                            '"bank_name":null,"branch_code":null,'
                            '"invoice_number":null,"payee_name":null}',
            mime='image/png', filename='blurry.png')
        self.assertFalse(out['ok'])
        vc.assert_called_once()
