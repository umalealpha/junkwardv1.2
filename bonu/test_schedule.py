"""Tests for the BONU schedule store — verbatim import + editable CRUD.

Synthetic fixtures only (no real member data)."""
import os
import tempfile
from decimal import Decimal

import openpyxl
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu import schedule
from bonu.models import BonuScheduleRow, BonuScheduleSheet
from bonu.schedule_views import (schedule_export, schedule_row_detail, schedule_rows,
                                 schedule_sheets)

User = get_user_model()


def _make_wb(path):
    import openpyxl
    wb = openpyxl.Workbook()
    # CLAIMS sheet: a title row, a spacer, then the header + rows (real shape)
    ws = wb.active
    ws.title = 'CLAIMS'
    ws.append(['Law Firm Name', 'Inv Date', 'Client Names', 'Case Matter',
               'Inv Amount (P)', 'Amount Paid (P)'])
    ws.append(['Firm A', '15/02/2025', 'Client One', 'Divorce', 1000, 1000])
    ws.append(['Firm B', '28/02/2025', 'Client Two', 'Custody', 2500.50, 0])
    ws.append([None, None, None, None, None, None])          # blank row → skipped
    # A second, differently-shaped sheet with no money column
    ws2 = wb.create_sheet('UNPAID FEES')
    ws2.append(['Claim Expenses', 'Bank', 'Acc No', 'KYC Status'])
    ws2.append(['Firm A', 'FNB', '62810840901', 'Compliant'])
    wb.save(path)


def _make_wb_real_shape(path):
    """The two shapes the real BONU workbook has and the synthetic fixture did not:
    a grand-total row sitting inside the data, and a matrix sheet whose header is
    the period row with a blank corner cell."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'CLAIMS'
    ws.append(['Law Firm Name', 'Inv Date', 'Inv Amount (P)'])
    ws.append(['Firm A', '15/02/2025', 1000])
    ws.append(['Firm B', '28/02/2025', 2500.50])
    ws.append(['Totals', None, 3500.50])           # the workbook's OWN total line
    ws.append([None, None, None])                  # the real file has this blank row
    ws2 = wb.create_sheet('SUMMARY')
    ws2.append([None, None, None])
    ws2.append([None, 'Jan-2025', 'Feb-2025'])     # header: corner blank → 2 cells
    ws2.append(['Total Revenue', 100, 200])        # first data row → 3 cells, one MORE
    ws2.append(['Admin Expenses', -50, -60])
    wb.save(path)


class RealShapeTests(TestCase):
    """Both of these passed on the synthetic fixture and were wrong on the real
    file — the fixture had no total row and no matrix sheet."""

    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, 'BONU PERFORMANCE REPORT REALSHAPE.xlsx')
        _make_wb_real_shape(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)

    def test_grand_total_row_is_not_counted_as_a_fee_note(self):
        claims = BonuScheduleSheet.objects.get(key='claims')
        self.assertEqual(claims.rows.count(), 3)               # 2 fee notes + the total line
        # Counting the total line as data doubles it: 3500.50 + 3500.50 = 7001.00
        self.assertEqual(schedule.sheet_total(claims), Decimal('3500.50'))
        self.assertEqual(schedule.stated_total(claims), Decimal('3500.50'))

    def test_a_sheet_with_no_total_line_states_none(self):
        summary = BonuScheduleSheet.objects.get(key='summary')
        self.assertIsNone(schedule.stated_total(summary))

    def test_matrix_header_is_the_period_row_not_the_first_data_row(self):
        summary = BonuScheduleSheet.objects.get(key='summary')
        self.assertEqual(summary.columns[1:], ['Jan-2025', 'Feb-2025'])
        # The Total Revenue line is DATA — reading it as the header loses it.
        labels = [r.cells[summary.columns[0]] for r in summary.rows.all().order_by('position')]
        self.assertEqual(labels, ['Total Revenue', 'Admin Expenses'])

    def test_reconciliation_reports_whether_the_detail_foots(self):
        recon = schedule.reconcile()
        claims = next(s for s in recon['sheets'] if s['key'] == 'claims')
        self.assertEqual(claims['total'], '3500.50')
        self.assertEqual(claims['stated_total'], '3500.50')
        self.assertIs(claims['foots'], True)


class ImportTests(TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, 'BONU PERFORMANCE REPORT TEST.xlsx')
        _make_wb(self.path)

    def test_import_preserves_every_column_and_row(self):
        schedule.import_workbook(self.path, source_note='test.xlsx', commit=True)
        claims = BonuScheduleSheet.objects.get(key='claims')
        self.assertEqual(claims.columns,
                         ['Law Firm Name', 'Inv Date', 'Client Names', 'Case Matter',
                          'Inv Amount (P)', 'Amount Paid (P)'])
        self.assertEqual(claims.amount_column, 'Inv Amount (P)')
        self.assertEqual(claims.rows.count(), 2)               # blank row dropped, data kept
        first = claims.rows.get(position=0)
        self.assertEqual(first.cells['Law Firm Name'], 'Firm A')
        self.assertEqual(first.cells['Client Names'], 'Client One')
        self.assertEqual(first.cells['Inv Amount (P)'], '1000')

    def test_amount_total_and_reconciliation(self):
        schedule.import_workbook(self.path, commit=True)
        claims = BonuScheduleSheet.objects.get(key='claims')
        self.assertEqual(schedule.sheet_total(claims), Decimal('3500.50'))
        rec = schedule.reconcile()
        sheet = next(s for s in rec['sheets'] if s['key'] == 'claims')
        self.assertEqual(sheet['total'], '3500.50')

    def test_no_ledger_lines_means_no_comparison_offered(self):
        """CFO 13-Aug-2026: with no ledger invoices there is nothing to compare the
        union's schedule WITH. Reporting a 'gap' equal to the whole schedule invents
        a finding, so the key is omitted and the screen drops the strip."""
        schedule.import_workbook(self.path, commit=True)
        self.assertNotIn('claims_reconciliation', schedule.reconcile())

    def test_reimport_replaces_rows_not_appends(self):
        schedule.import_workbook(self.path, commit=True)
        schedule.import_workbook(self.path, commit=True)
        self.assertEqual(BonuScheduleSheet.objects.get(key='claims').rows.count(), 2)

    def test_sheet_without_amount_column_has_zero_total(self):
        schedule.import_workbook(self.path, commit=True)
        unpaid = BonuScheduleSheet.objects.get(key='unpaid-fees')
        self.assertEqual(unpaid.amount_column, '')
        self.assertEqual(schedule.sheet_total(unpaid), Decimal('0'))


class CrudApiTests(TestCase):
    def setUp(self):
        self.rf = APIRequestFactory()
        self.user = User.objects.create_user('acc', 'acc@x.co', 'pw', is_superuser=True, is_staff=True)
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'wb.xlsx')
        _make_wb(p)
        schedule.import_workbook(p, commit=True)
        self.claims = BonuScheduleSheet.objects.get(key='claims')

    def test_list_sheets(self):
        req = self.rf.get('/api/v1/bonu/schedule/')
        force_authenticate(req, user=self.user)
        resp = schedule_sheets(req)
        self.assertEqual(resp.status_code, 200)
        keys = {s['key'] for s in resp.data['sheets']}
        self.assertIn('claims', keys)

    def test_add_edit_delete_row(self):
        # add
        req = self.rf.post(f'/api/v1/bonu/schedule/claims/rows/',
                           {'cells': {'Law Firm Name': 'Firm C', 'Inv Amount (P)': '750'}},
                           format='json')
        force_authenticate(req, user=self.user)
        resp = schedule_rows(req, key='claims')
        self.assertEqual(resp.status_code, 201)
        rid = resp.data['id']
        self.assertEqual(resp.data['cells']['Law Firm Name'], 'Firm C')
        self.assertEqual(BonuScheduleRow.objects.filter(sheet=self.claims).count(), 3)

        # edit
        req = self.rf.patch(f'/api/v1/bonu/schedule/rows/{rid}/',
                            {'cells': {'Law Firm Name': 'Firm C', 'Inv Amount (P)': '800'}},
                            format='json')
        force_authenticate(req, user=self.user)
        resp = schedule_row_detail(req, row_id=rid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['cells']['Inv Amount (P)'], '800')

        # delete
        req = self.rf.delete(f'/api/v1/bonu/schedule/rows/{rid}/')
        force_authenticate(req, user=self.user)
        resp = schedule_row_detail(req, row_id=rid)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(BonuScheduleRow.objects.filter(sheet=self.claims).count(), 2)

    def test_status_field_is_normalised_on_save(self):
        """CFO 13-Aug-2026 mistake list — 'Paid' and 'paid' were two values for
        the same thing, silently splitting every group-by. Trim + title-case on
        save fixes it going forward."""
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Amount (P)', 'Status'])
        ws.append(['X', 1, 'Paid'])
        d = tempfile.mkdtemp(); p = os.path.join(d, 'st.xlsx')
        wb.save(p)
        schedule.import_workbook(p, commit=True, wipe=True)
        # A caller submits ragged casing + trailing space.
        req = self.rf.post(f'/api/v1/bonu/schedule/claims/rows/',
                           {'cells': {'Law Firm Name': 'Y',
                                      'Inv Amount (P)': '2',
                                      'Status': '  paid '}},
                           format='json')
        force_authenticate(req, user=self.user)
        resp = schedule_rows(req, key='claims')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['cells']['Status'], 'Paid')

    def test_acronyms_and_free_text_survive_the_normaliser(self):
        """Fable 5, 13-Aug-2026: `.capitalize()` lower-cases every non-first
        letter — 'KYC Pending' became 'Kyc Pending', and 'Case Matter' was
        wrongly listed as enumerated (it holds free text)."""
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Amount (P)', 'KYC Status', 'Case Matter'])
        ws.append(['X', 1, 'Compliant', 'Divorce'])
        d = tempfile.mkdtemp(); p = os.path.join(d, 'acr.xlsx')
        wb.save(p); schedule.import_workbook(p, commit=True, wipe=True)
        req = self.rf.post(f'/api/v1/bonu/schedule/claims/rows/',
                           {'cells': {'Law Firm Name': 'Y',
                                      'Inv Amount (P)': '2',
                                      'KYC Status': 'KYC pending',
                                      'Case Matter': 'debt Recovery for Estate'}},
                           format='json')
        force_authenticate(req, user=self.user)
        resp = schedule_rows(req, key='claims')
        self.assertEqual(resp.status_code, 201)
        # KYC survives the enumerated normaliser.
        self.assertEqual(resp.data['cells']['KYC Status'], 'KYC Pending')
        # Case Matter is free text — the reader's own words come back verbatim.
        self.assertEqual(resp.data['cells']['Case Matter'],
                         'debt Recovery for Estate')

    def _rows(self, **params):
        req = self.rf.get('/api/v1/bonu/schedule/claims/rows/', params)
        force_authenticate(req, user=self.user)
        return schedule_rows(req, key='claims')

    def test_filter_text_search(self):
        r = self._rows(q='Firm B')
        self.assertEqual(r.data['total'], 1)
        self.assertEqual(r.data['rows'][0]['cells']['Law Firm Name'], 'Firm B')

    def test_filter_by_firm_and_firms_list(self):
        r = self._rows()
        self.assertEqual(r.data['firm_column'], 'Law Firm Name')
        self.assertEqual(set(r.data['firms']), {'Firm A', 'Firm B'})
        r = self._rows(firm='Firm A')
        self.assertEqual(r.data['total'], 1)
        self.assertEqual(r.data['rows'][0]['cells']['Law Firm Name'], 'Firm A')

    def test_filter_amount_range_and_matched_total(self):
        r = self._rows(amount_min='2000')
        self.assertEqual(r.data['total'], 1)                 # only the 2500.50 row
        self.assertEqual(r.data['filtered_total'], '2500.50')
        r = self._rows(amount_max='1500')
        self.assertEqual(r.data['total'], 1)                 # only the 1000 row
        self.assertEqual(r.data['filtered_total'], '1000.00')

    def test_edit_ignores_unknown_columns(self):
        row = self.claims.rows.first()
        req = self.rf.patch(f'/api/v1/bonu/schedule/rows/{row.id}/',
                            {'cells': {'Law Firm Name': 'X', 'HACK': 'nope'}}, format='json')
        force_authenticate(req, user=self.user)
        resp = schedule_row_detail(req, row_id=str(row.id))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('HACK', resp.data['cells'])


class InsightsTests(TestCase):
    def setUp(self):
        from bonu.models import BonuInvoice, LawFirm
        import openpyxl, os, tempfile
        self.rf = APIRequestFactory()
        self.user = User.objects.create_user('ins', 'ins@x.co', 'pw', is_superuser=True, is_staff=True)
        d = tempfile.mkdtemp(); p = os.path.join(d, 'ins.xlsx')
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Date', 'Invoice/Referance No:', 'Client Names',
                   'Case Matter', 'Invoice Month', 'Inv Amount (P)'])
        ws.append(['Firm A', '10/03/2025', 'INV-1', 'Mpho Kego', 'Divorce', 'Mar', 100000])
        ws.append(['Firm A', '12/03/2025', 'INV-1', 'Mpho Kego', 'Divorce', 'Mar', 100000])  # dup ref + fam
        ws.append(['Firm B', '05/05/2025', 'INV-2', 'Retainer Fees', 'Misc', 'May', 30000])   # retainer excluded
        ws.append(['Firm B', '06/06/2025', 'INV-3', 'Tumi Rasi', 'Debt', 'Jun', 5000])
        wp = wb.create_sheet('PREMIUMS')
        wp.append(['Invoice Month', 'Total Premium Amount'])
        wp.append(['Jan', 100]); wp.append(['Feb', 200])
        wb.save(p)
        schedule.import_workbook(p, commit=True)
        firm = LawFirm.objects.create(name='Firm B')
        BonuInvoice.objects.create(firm=firm, invoice_number='INV-3', invoice_date='2025-06-06', total=5000)
        BonuInvoice.objects.create(firm=firm, invoice_number='INV-2', invoice_date='2025-05-05', total=31000)  # mismatch

    def test_gap_lists_claims_not_in_ledger(self):
        from bonu.schedule_insights import gap_to_ledger
        g = gap_to_ledger()
        refs = {u['ref'] for u in g['unmatched']}
        self.assertIn('INV-1', refs)                 # not in ledger
        self.assertNotIn('INV-3', refs)              # in ledger → matched

    def test_no_ledger_bills_means_no_gap_is_offered(self):
        """CFO 13-Aug-2026. With the ledger empty, schedule − 0 put the WHOLE
        schedule on screen as a gap 'not yet in the ledger'. Nothing to compare with
        is not the same as everything missing."""
        from bonu.models import BonuInvoice
        from bonu.schedule_insights import bill_matches, gap_to_ledger
        BonuInvoice.objects.all().delete()
        self.assertFalse(gap_to_ledger()['available'])
        self.assertFalse(bill_matches()['available'])

    def test_workbook_total_row_is_not_data_in_any_insight(self):
        """The workbook's own 'Totals' line reached every insight through _rows(), so
        it double-counted all of them at once: the schedule total read double, and
        'Totals' appeared in the league table as the biggest law firm we use."""
        import os
        import tempfile

        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Date', 'Invoice/Referance No:', 'Client Names',
                   'Case Matter', 'Invoice Month', 'Inv Amount (P)'])
        ws.append(['Firm A', '10/03/2025', 'INV-9', 'Mpho Kego', 'Divorce', 'Mar', 1000])
        ws.append(['Firm B', '11/03/2025', 'INV-8', 'Tumi Rasi', 'Debt', 'Mar', 500])
        ws.append(['Totals', None, None, None, None, None, 1500])     # the workbook's own line
        p = os.path.join(tempfile.mkdtemp(), 'tot.xlsx'); wb.save(p)
        schedule.import_workbook(p, commit=True, wipe=True)

        from bonu.schedule_insights import cost_by_firm
        rows = cost_by_firm()['rows']
        names = [r['name'] for r in rows]
        self.assertNotIn('Totals', names)                    # not a law firm
        self.assertEqual(sum(Decimal(r['total']) for r in rows), Decimal('1500'))

    def test_member_over_limit_excludes_retainer(self):
        from bonu.schedule_insights import member_limits
        m = member_limits()
        overs = {o['member']: o for o in m['over']}
        self.assertIn('Mpho Kego', overs)            # 100k+100k = 200k > 90k
        self.assertNotIn('Retainer Fees', overs)

    def test_duplicate_reference_detected(self):
        from bonu.schedule_insights import duplicate_claims
        d = duplicate_claims()
        refs = {g['items'][0]['ref'] for g in d['by_reference']}
        self.assertIn('INV-1', refs)

    def test_same_reference_at_DIFFERENT_firms_is_not_double_billing(self):
        """Live data flagged reference '003' across Chikati, Gape April and Mbikiwa as
        one bill paid three times. Three firms each numbering a fee note '003' is
        three firms using a simple sequence — and it inflated 'amount at risk' with
        money nobody could ever claim back."""
        import os
        import tempfile

        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Date', 'Invoice/Referance No:', 'Client Names',
                   'Case Matter', 'Invoice Month', 'Inv Amount (P)'])
        ws.append(['Chikati and Partners', '10/03/2025', '003', 'A', 'Divorce', 'Mar', 10000])
        ws.append(['Gape April Attorneys', '11/03/2025', '003', 'B', 'Divorce', 'Mar', 10000])
        ws.append(['Mbikiwa Legal Practice', '12/03/2025', '003', 'C', 'Divorce', 'Mar', 10000])
        p = os.path.join(tempfile.mkdtemp(), 'refs.xlsx'); wb.save(p)
        schedule.import_workbook(p, commit=True, wipe=True)

        from bonu.schedule_insights import duplicate_claims
        d = duplicate_claims()
        self.assertEqual(d['by_reference'], [])
        self.assertEqual(Decimal(d['amount_at_risk_ref']), Decimal('0.00'))

    def test_premium_gaps_flags_missing_months(self):
        from bonu.schedule_insights import premium_gaps
        pg = premium_gaps()
        self.assertEqual(pg['present_count'], 2)      # Jan, Feb
        self.assertIn('Mar', pg['missing'])

    def test_bill_mismatch_flagged(self):
        from bonu.schedule_insights import bill_matches
        b = bill_matches()
        mrefs = {m['ref'] for m in b['mismatches']}
        self.assertIn('INV-2', mrefs)                 # schedule 30000 vs bill 31000

    def test_export_csv(self):
        req = self.rf.get('/x'); force_authenticate(req, user=self.user)
        resp = schedule_export(req, key='claims')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        self.assertIn(b'Law Firm Name', resp.content)

    def test_upload_preview_diff_does_not_write(self):
        import openpyxl, os, tempfile
        before = BonuScheduleSheet.objects.get(key='claims').rows.count()
        d = tempfile.mkdtemp(); p = os.path.join(d, 'new.xlsx')
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Date', 'Invoice/Referance No:', 'Client Names',
                   'Case Matter', 'Invoice Month', 'Inv Amount (P)'])
        ws.append(['Firm A', '10/03/2025', 'INV-1', 'Mpho Kego', 'Divorce', 'Mar', 100000])
        ws.append(['Firm Z', '01/07/2025', 'INV-9', 'New Client', 'Debt', 'Jul', 999])  # added
        wb.save(p)
        from bonu.schedule import diff_workbook
        diffs = {d['key']: d for d in diff_workbook(p)}
        self.assertGreaterEqual(diffs['claims']['added'], 1)
        self.assertEqual(BonuScheduleSheet.objects.get(key='claims').rows.count(), before)  # no write


class FabeFixTests(TestCase):
    """Regressions for the /fabe fixes (12-Aug-2026)."""
    def setUp(self):
        self.rf = APIRequestFactory()
        self.user = User.objects.create_user('fx', 'fx@x.co', 'pw', is_superuser=True, is_staff=True)

    def _wb_bytes(self, rows):
        import io, openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Date', 'Invoice/Referance No:', 'Client Names',
                   'Case Matter', 'Invoice Month', 'Inv Amount (P)'])
        for r in rows:
            ws.append(r)
        b = io.BytesIO(); wb.save(b); return b.getvalue()

    def test_member_filter_keeps_name_containing_bonu_substring(self):
        # "Keabonu Tau" contains 'bonu' as a substring but is a real member — must NOT be dropped.
        from bonu import schedule
        from bonu.schedule_insights import member_limits
        import os, tempfile
        p = os.path.join(tempfile.mkdtemp(), 'm.xlsx')
        open(p, 'wb').write(self._wb_bytes([
            ['Firm A', '10/03/2025', 'INV-1', 'Keabonu Tau', 'Divorce', 'Mar', 100000],
            ['Firm A', '10/03/2025', 'INV-2', 'Retainer Fees', 'Misc', 'Mar', 50000],
        ]))
        schedule.import_workbook(p, commit=True)
        overs = {o['member'] for o in member_limits()['over']}
        self.assertIn('Keabonu Tau', overs)
        self.assertNotIn('Retainer Fees', overs)

    def test_csv_export_escapes_formula_injection(self):
        from bonu.schedule_views import _csv_safe
        self.assertEqual(_csv_safe('=CMD()'), "'=CMD()")
        self.assertEqual(_csv_safe('+1'), "'+1")
        self.assertEqual(_csv_safe('@x'), "'@x")
        self.assertEqual(_csv_safe('-SUM(A1)'), "'-SUM(A1)")   # non-numeric leading -
        self.assertEqual(_csv_safe('-1234.50'), '-1234.50')     # plain negative number untouched
        self.assertEqual(_csv_safe('Chikati'), 'Chikati')

    def test_upload_preview_then_commit_via_request(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from bonu.schedule_views import schedule_upload
        from bonu.models import BonuScheduleSheet
        data = self._wb_bytes([['Firm A', '10/03/2025', 'INV-1', 'X', 'Div', 'Mar', 10]])
        # preview → no write
        f = SimpleUploadedFile('wb.xlsx', data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        req = self.rf.post('/api/v1/bonu/schedule/upload/?preview=1', {'file': f})
        force_authenticate(req, user=self.user)
        resp = schedule_upload(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['preview'])
        self.assertFalse(BonuScheduleSheet.objects.filter(key='claims').exists())
        # commit → writes
        f2 = SimpleUploadedFile('wb.xlsx', data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        req2 = self.rf.post('/api/v1/bonu/schedule/upload/', {'file': f2})
        force_authenticate(req2, user=self.user)
        resp2 = schedule_upload(req2)
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.data['imported'])
        self.assertEqual(BonuScheduleSheet.objects.get(key='claims').rows.count(), 1)


class WipeReloadTests(TestCase):
    def _wb(self, path, with_empty=False):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'CLAIMS'
        ws.append(['Law Firm Name', 'Inv Date', 'Invoice/Referance No:', 'Client Names',
                   'Case Matter', 'Invoice Month', 'Inv Amount (P)'])
        ws.append(['Firm A', '10/03/2025', 'INV-1', 'X', 'Div', 'Mar', 100])
        if with_empty:
            wb.create_sheet('Sheet1')          # totally blank tab
        wb.save(path)

    def test_empty_sheet_skipped(self):
        from bonu import schedule
        from bonu.models import BonuScheduleSheet
        import os, tempfile
        p = os.path.join(tempfile.mkdtemp(), 'e.xlsx'); self._wb(p, with_empty=True)
        schedule.import_workbook(p, commit=True)
        keys = set(BonuScheduleSheet.objects.values_list('key', flat=True))
        self.assertIn('claims', keys)
        self.assertNotIn('sheet1', keys)       # blank tab never created

    def test_wipe_refused_on_empty_workbook(self):
        # A file that parses to zero data rows must NOT wipe live data.
        from bonu import schedule
        from bonu.models import BonuScheduleSheet
        import openpyxl, os, tempfile
        BonuScheduleSheet.objects.create(key='live', title='LIVE', columns=['a'], order=1)
        p = os.path.join(tempfile.mkdtemp(), 'blank.xlsx')
        wb = openpyxl.Workbook(); wb.active.title = 'Blank'; wb.save(p)   # only a blank tab
        with self.assertRaises(ValueError):
            schedule.import_workbook(p, commit=True, wipe=True)
        self.assertTrue(BonuScheduleSheet.objects.filter(key='live').exists())   # untouched

    def test_wipe_removes_stale_sheets(self):
        from bonu import schedule
        from bonu.models import BonuScheduleSheet
        import os, tempfile
        BonuScheduleSheet.objects.create(key='stale', title='STALE', columns=['a'], order=9)
        p = os.path.join(tempfile.mkdtemp(), 'w.xlsx'); self._wb(p)
        schedule.import_workbook(p, commit=True, wipe=True)
        keys = set(BonuScheduleSheet.objects.values_list('key', flat=True))
        self.assertEqual(keys, {'claims'})     # stale gone, only the new file's sheet
