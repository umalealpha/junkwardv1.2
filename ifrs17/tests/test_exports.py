"""
ifrs17/tests/test_exports.py — the disclosures actually leave the building.

Auditors get documents, not screens. These prove the xlsx, the Word note and the
pre-filled data-request workbook generate real, openable files carrying the signed
figures — not that a function returned without error.
"""
import io

from django.test import SimpleTestCase

from ifrs17.data_request import workbook_bytes as data_request_bytes
from ifrs17.engine import Levers, compute
from ifrs17.export import disclosure_docx, workbook_bytes


class ExportTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.c = compute(Levers.base(), year='FY2026')

    def test_the_xlsx_opens_and_carries_the_signed_revenue(self):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(workbook_bytes(self.c)))
        # A sheet per disclosure table, plus the cover.
        self.assertGreaterEqual(len(wb.sheetnames), 10)
        self.assertIn('Cover', wb.sheetnames)
        # The signed insurance revenue must appear somewhere in the pack.
        found = any(
            cell.value == 133416295
            for ws in wb.worksheets for row in ws.iter_rows() for cell in row
            if isinstance(cell.value, (int, float)))
        self.assertTrue(found, 'signed insurance revenue 133,416,295 not in the workbook')

    def test_the_docx_opens_and_names_the_company(self):
        from docx import Document
        doc = Document(io.BytesIO(disclosure_docx(self.c)))
        text = '\n'.join(p.text for p in doc.paragraphs)
        self.assertIn('Alpha Direct Insurance Company', text)
        self.assertIn('paragraphs 100 to 105', text)
        # The disclosure tables are real Word tables, not pasted text.
        self.assertGreaterEqual(len(doc.tables), 10)

    def test_the_data_request_has_empiricas_tabs(self):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data_request_bytes(self.c)))
        for tab in ('Instructions', '1. Premium & UPR', '2. Claims & OCR',
                    '3. IBNR CHER & Recon', '4. Reinsurance', '5. Expenses',
                    '6. Checklist'):
            self.assertIn(tab, wb.sheetnames, f'missing tab {tab}')

    def test_the_data_request_premium_ties_to_the_valuation(self):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data_request_bytes(self.c)))
        ws = wb['1. Premium & UPR']
        total_row = [r for r in ws.iter_rows(values_only=True)
                     if r and str(r[0]).upper() == 'TOTAL']
        self.assertTrue(total_row)
        # The eight segments sum to the reported GWP within the report's own
        # class-allocation rounding (DQ-14: "differ by BWP 5 ... because of
        # rounding within the class allocation").
        self.assertLessEqual(abs(round(total_row[0][1]) - 133416295), 6)

    def test_the_checklist_carries_the_disclosed_differences(self):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data_request_bytes(self.c)))
        refs = {r[0] for r in wb['6. Checklist'].iter_rows(values_only=True) if r}
        # The JBB commission and the Health items must be on the actuary's list.
        self.assertIn('DQ-01', refs)
        self.assertIn('DQ-03', refs)

    def test_a_moved_lever_changes_the_export(self):
        """The document must follow the sliders, not a frozen snapshot."""
        from openpyxl import load_workbook
        moved = compute(Levers(jbb_commission_pct=__import__('decimal').Decimal('0.41')))
        wb = load_workbook(io.BytesIO(workbook_bytes(moved)))
        cover = wb['Cover']
        text = ' '.join(str(c.value) for row in cover.iter_rows() for c in row if c.value)
        self.assertIn('not been recognised', text)
