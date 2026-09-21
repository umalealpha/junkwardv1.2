"""Tests for the guided BONU capture forms (bonu/capture.py).

Synthetic fixtures only — no real member data. Proves the four forms write one
well-formed row into the right sheet, enforce required fields, keep the shared
grid path (totals move), and create the Other-expenses sheet on first use.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu import schedule
from bonu.capture import OTHER_KEY, capture_forms, capture_submit
from bonu.models import BonuScheduleSheet

User = get_user_model()


def _seed_sheets():
    """The three sheets the forms target, in the real shape (amount column + a
    Totals row that must not be double-counted)."""
    def mk(key, title, columns, amount, rows):
        s = BonuScheduleSheet.objects.create(key=key, title=title, columns=columns,
                                             amount_column=amount, order=0)
        for i, cells in enumerate(rows):
            s.rows.create(position=i, cells=cells)
        return s
    mk('premium-2026-27', 'Premium', ['Date', 'Invoice Month', 'Policyholder Name',
                                      'Gross Written Premium', 'Vat On Total Premium',
                                      'Commission', 'Vat On Commission',
                                      'Total Premium Amount', 'Payment Status',
                                      'Date Received'],
       'Total Premium Amount',
       [{'Total Premium Amount': '1000', 'Policyholder Name': 'BONU'}])
    mk('claims', 'Claims', ['Law Firm Name', 'Inv Date', 'Invoice/Referance No:',
                            'Client Names', 'Case Matter', 'Invoice Month',
                            'Inv Amount (P)', 'Discount', 'Amount Paid (P)',
                            'Payment Date', 'Status'],
       'Inv Amount (P)', [{'Law Firm Name': 'Firm A', 'Inv Amount (P)': '500'}])
    mk('admin-expenses', 'Admin', ['Date', 'Category', 'Description', 'Invoice Issued',
                                   'Payment Method', 'Amount (BWP)', 'Status'],
       'Amount (BWP)', [{'Description': 'Old', 'Amount (BWP)': '200'}])


class CaptureTests(TestCase):
    def setUp(self):
        self.rf = APIRequestFactory()
        self.user = User.objects.create_user('acc', 'acc@x.co', 'pw',
                                              is_superuser=True, is_staff=True)
        _seed_sheets()

    def _submit(self, form, values):
        req = self.rf.post(f'/api/v1/bonu/capture/{form}/', {'values': values},
                           format='json')
        force_authenticate(req, user=self.user)
        return capture_submit(req, form_key=form)

    def test_forms_listed_with_fields_and_totals(self):
        req = self.rf.get('/api/v1/bonu/capture/')
        force_authenticate(req, user=self.user)
        resp = capture_forms(req)
        self.assertEqual(resp.status_code, 200)
        forms = {f['key']: f for f in resp.data['forms']}
        self.assertEqual(set(forms), {'revenue', 'supplier', 'admin', 'other'})
        # every form ships its fields and the current running total of its sheet
        self.assertTrue(all(f['fields'] for f in forms.values()))
        self.assertEqual(forms['supplier']['sheet_total'], '500.00')
        # the Other-expenses sheet does not exist until the first capture
        self.assertFalse(forms['other']['sheet_exists'])

    def test_revenue_row_lands_in_premium_sheet_and_moves_total(self):
        resp = self._submit('revenue', {
            'Date': '2026-08-17', 'Invoice Month': 'August', 'Policyholder Name': 'BONU',
            'Gross Written Premium': '320302.70', 'Vat On Total Premium': '44842.38',
            'Total Premium Amount': '365145.08', 'Payment Status': 'Paid'})
        self.assertEqual(resp.status_code, 201)
        sheet = BonuScheduleSheet.objects.get(key='premium-2026-27')
        # started at 1000, +365145.08
        self.assertEqual(schedule.sheet_total(sheet), Decimal('366145.08'))
        row = sheet.rows.order_by('-position').first()
        self.assertEqual(row.cells['Gross Written Premium'], '320302.70')
        self.assertEqual(row.updated_by_email, 'acc@x.co')

    def test_supplier_row_lands_in_claims(self):
        resp = self._submit('supplier', {
            'Law Firm Name': 'Firm Z', 'Inv Date': '2026-08-01',
            'Invoice/Referance No:': 'INV-9', 'Client Names': 'Member 12',
            'Inv Amount (P)': '1250.00', 'Status': 'Unpaid'})
        self.assertEqual(resp.status_code, 201)
        sheet = BonuScheduleSheet.objects.get(key='claims')
        self.assertEqual(schedule.sheet_total(sheet), Decimal('1750.00'))

    def test_supplier_requires_the_customer(self):
        # firm + amount present, but no customer attached -> refused
        resp = self._submit('supplier', {
            'Law Firm Name': 'Firm Z', 'Inv Date': '2026-08-01',
            'Invoice/Referance No:': 'INV-9', 'Inv Amount (P)': '1250.00'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Customer / member', resp.data['detail'])

    def test_other_expense_creates_the_sheet_on_first_use(self):
        self.assertFalse(BonuScheduleSheet.objects.filter(key=OTHER_KEY).exists())
        resp = self._submit('other', {
            'Date': '2026-08-17', 'Category': 'Bank charges',
            'Description': 'August EFT fees', 'Amount (BWP)': '85.50', 'Status': 'Paid'})
        self.assertEqual(resp.status_code, 201)
        sheet = BonuScheduleSheet.objects.get(key=OTHER_KEY)
        self.assertEqual(schedule.sheet_total(sheet), Decimal('85.50'))
        # second one reuses the same sheet, does not create a duplicate
        self._submit('other', {'Date': '2026-08-18', 'Category': 'Printing',
                               'Description': 'letterheads', 'Amount (BWP)': '14.50'})
        self.assertEqual(BonuScheduleSheet.objects.filter(key=OTHER_KEY).count(), 1)
        self.assertEqual(schedule.sheet_total(sheet), Decimal('100.00'))

    def test_required_fields_are_enforced(self):
        # amount + firm missing on a supplier bill
        resp = self._submit('supplier', {'Client Names': 'Someone'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Supplier / law firm', resp.data['detail'])
        self.assertEqual(BonuScheduleSheet.objects.get(key='claims').rows.count(), 1)

    def test_unknown_form_rejected(self):
        resp = self._submit('nonsense', {'x': '1'})
        self.assertEqual(resp.status_code, 400)

    def _supplier(self, **over):
        vals = {'Law Firm Name': 'Firm Z', 'Inv Date': '2026-08-01',
                'Invoice/Referance No:': 'INV-1', 'Client Names': 'Member A',
                'Inv Amount (P)': '1000.00', 'Status': 'Unpaid'}
        vals.update(over)
        return self._submit('supplier', vals)

    def test_duplicate_invoice_reference_is_blocked(self):
        self.assertEqual(self._supplier().status_code, 201)
        # same reference again -> refused, and nothing added
        resp = self._supplier(**{'Inv Amount (P)': '999.00'})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['code'], 'DUPLICATE_INVOICE')
        self.assertEqual(BonuScheduleSheet.objects.get(key='claims').rows.count(), 2)  # 1 seed + 1

    def test_garbage_amount_is_rejected_not_silently_zeroed(self):
        # 'P15k' must not slip past as 0 — the cap reads the same parser the total does.
        resp = self._supplier(**{'Invoice/Referance No:': 'G-1', 'Inv Amount (P)': 'P15k'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('must be a number', resp.data['detail'])
        # a normal 'P15,000.00' is accepted (parser strips P and commas)
        ok = self._supplier(**{'Invoice/Referance No:': 'G-2', 'Inv Amount (P)': 'P15,000.00'})
        self.assertEqual(ok.status_code, 201)

    def test_grid_add_row_enforces_claims_guards(self):
        from bonu.schedule_views import schedule_rows
        def grid(cells):
            req = self.rf.post('/x', {'cells': cells}, format='json')
            force_authenticate(req, user=self.user)
            return schedule_rows(req, key='claims')
        # duplicate reference on the raw grid is blocked too
        self.assertEqual(grid({'Invoice/Referance No:': 'GR-1', 'Client Names': 'M1',
                               'Inv Amount (P)': '100'}).status_code, 201)
        dup = grid({'Invoice/Referance No:': 'GR-1', 'Client Names': 'M1', 'Inv Amount (P)': '5'})
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(dup.data['code'], 'DUPLICATE_INVOICE')
        # over-cap on the raw grid is blocked (no override on the grid)
        over = grid({'Invoice/Referance No:': 'GR-2', 'Client Names': 'M1', 'Inv Amount (P)': '95000'})
        self.assertEqual(over.status_code, 409)
        self.assertEqual(over.data['code'], 'CAP_EXCEEDED')

    def test_member_cap_blocks_then_allows_with_reason(self):
        # First bill: P80,000 for Member Cap — under the P90k cap, records fine.
        r1 = self._supplier(**{'Invoice/Referance No:': 'C-1', 'Client Names': 'Member Cap',
                               'Inv Amount (P)': '80000.00'})
        self.assertEqual(r1.status_code, 201)
        # Second: P15,000 takes them to P95,000 -> blocked.
        r2 = self._supplier(**{'Invoice/Referance No:': 'C-2', 'Client Names': 'Member Cap',
                               'Inv Amount (P)': '15000.00'})
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(r2.data['code'], 'CAP_EXCEEDED')
        self.assertEqual(r2.data['remaining'], '10000.00')
        # Override with no reason -> refused (override is a sibling of values).
        over_vals = {'Law Firm Name': 'Firm Z', 'Inv Date': '2026-08-01',
                     'Invoice/Referance No:': 'C-2', 'Client Names': 'Member Cap',
                     'Inv Amount (P)': '15000.00', 'Status': 'Unpaid'}
        req3 = self.rf.post('/x', {'values': over_vals, 'override': True}, format='json')
        force_authenticate(req3, user=self.user)
        r3 = capture_submit(req3, form_key='supplier')
        self.assertEqual(r3.status_code, 400)
        self.assertEqual(r3.data['code'], 'OVERRIDE_REASON_REQUIRED')
        # Override with a reason -> recorded, reason kept on the row note.
        req = self.rf.post('/x', {'values': {
            'Law Firm Name': 'Firm Z', 'Inv Date': '2026-08-01', 'Invoice/Referance No:': 'C-2',
            'Client Names': 'Member Cap', 'Inv Amount (P)': '15000.00', 'Status': 'Unpaid'},
            'override': True, 'override_reason': 'union-approved appeal'}, format='json')
        force_authenticate(req, user=self.user)
        r4 = capture_submit(req, form_key='supplier')
        self.assertEqual(r4.status_code, 201)
        self.assertIn('union-approved appeal', r4.data['row']['note'])

    def test_invoice_reader_prefills_figures_but_never_the_customer(self):
        # A text invoice (CSV path — local, no AI needed for the header).
        from bonu.capture import read_supplier_invoice
        blob = (b'Fee Note\nInvoice No: INV-2026-77\nDate: 2026-08-05\n'
                b'Divorce matter - consultation, 3500.00\nTotal, 3500.00\n')
        out = read_supplier_invoice('bill.csv', blob)
        self.assertTrue(out['ok'])
        v = out['values']
        self.assertEqual(v['Invoice/Referance No:'], 'INV-2026-77')
        self.assertEqual(v['Inv Date'], '2026-08-05')
        self.assertEqual(v['Invoice Month'], 'August')
        self.assertEqual(v['Inv Amount (P)'], '3500.00')
        # The customer is NEVER auto-filled — the accountant attaches it.
        self.assertNotIn('Client Names', v)
        self.assertNotIn('Law Firm Name', v)

    def test_invoice_reader_flags_a_scan_for_manual_entry(self):
        from bonu.capture import read_supplier_invoice
        # A .pdf blob with no text layer -> extract_text returns nothing.
        out = read_supplier_invoice('scan.pdf', b'%PDF-1.4 not-really-a-pdf')
        self.assertFalse(out['ok'])
        self.assertTrue(out['needs_manual'])
        self.assertEqual(out['values'], {})
