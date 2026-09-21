"""
procurement/test_po_pdf.py — supplier-facing PO print (procurement/pdf.py).

Adversarial tests for generate_po_pdf(po) -> bytes, asserting on the RENDERED
PDF via pdfplumber text extraction (not on internals). Guards the three fixed
print bugs plus the guarantees a production ERP PO print makes (mined from
erpnext's purchase_order_standard print format + bigcapital's paper
templates):

  * lines print in their deliberate creation order — the excess deduction
    LAST, never floating to the top (old order_by('id') on a UUID pk was
    effectively random);
  * the Taxes column shows each line's REAL tax — '—' for a no-VAT line
    (the excess), 'VAT 14%' for a standard-rated line — never a blanket
    label;
  * long descriptions wrap inside their column instead of overflowing;
  * &, <, > in ANY dynamic text (descriptions, supplier names, claim refs,
    justification) survive to the printed page — reportlab's Paragraph
    silently EATS unescaped '<...>' rather than raising;
  * multi-page POs repeat the lines-table header and keep the totals after
    the last line;
  * the totals block prints the PO's STORED header totals (P-prefixed) and
    never fabricates a 14% VAT on a PO whose lines carry none;
  * repair-order T&Cs appear on claims POs only;
  * degenerate POs (no lines / negative-only line) still render.

Fixtures are DB-real but minimal, mirroring test_claims_api.py: BWP, one
Company, the standard 14% VAT TaxRate, a vendor Contact, then PurchaseOrder
+ PurchaseOrderLine rows created directly.
"""

import io
import re
from datetime import date, timedelta
from decimal import Decimal

import pdfplumber
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from billing.models import Contact
from core.models import Company, Currency, TaxRate
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.pdf import generate_po_pdf


def _p(v) -> str:
    """Expected money formatting on the print: P-prefix, thousands commas."""
    n = Decimal(str(v))
    sign = '-' if n < 0 else ''
    return f'{sign}P{abs(n):,.2f}'


class POPdfRenderTest(TestCase):
    """generate_po_pdf — assertions on the extracted text of the real PDF."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TESTP', name='PDF Test Co.')
        cls.vat = TaxRate.objects.create(
            tax_code='VAT_STD', name='Standard Rate 14%',
            rate=Decimal('14.00'), is_active=True,
            effective_from=date(2026, 1, 1),
        )
        cls.supplier = Contact.objects.create(
            name='Carfil Panel Beaters (Pty) Ltd', contact_type='vendor',
            company=cls.company,
        )
        cls.user = User.objects.create_user(
            'pdfuser', password='x', first_name='Prathap', last_name='G',
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_po(self, line_specs, department='claims', supplier=None,
                 **overrides):
        """
        Create a PO + lines in the given sequence. Each spec:
            {'description': str, 'unit_price': num,
             'qty': num (default 1), 'vat': bool (default False)}
        created_at is forced to a strictly increasing series so the
        order_by('created_at', 'id') print contract is what's under test —
        not the microsecond resolution of auto_now_add on this machine.
        """
        po = PurchaseOrder.objects.create(
            department=department,
            supplier=supplier or self.supplier,
            company=self.company,
            issue_date=date(2026, 7, 1),
            created_by=self.user,
            **overrides,
        )
        base = timezone.now()
        for i, spec in enumerate(line_specs):
            ln = PurchaseOrderLine.objects.create(
                purchase_order=po,
                description=spec['description'],
                quantity=Decimal(str(spec.get('qty', 1))),
                unit_price=Decimal(str(spec['unit_price'])),
                tax_code=self.vat if spec.get('vat') else None,
            )
            PurchaseOrderLine.objects.filter(pk=ln.pk).update(
                created_at=base + timedelta(seconds=i),
            )
        po.recalculate_totals()
        po.save()
        return po

    def _pages(self, po):
        """Render and return the per-page extracted text list."""
        data = generate_po_pdf(po)
        self.assertTrue(data.startswith(b'%PDF'), 'not a PDF payload')
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return [page.extract_text() or '' for page in pdf.pages]

    @staticmethod
    def _tokens_in_order(text, phrase) -> bool:
        """True when every whitespace-separated token of `phrase` appears in
        `text` in order. A wrapped table cell interleaves with its neighbour
        columns in pdfplumber's line-based extraction, so exact substring
        matching is impossible for multi-line cells — token order is the
        extraction-stable equivalent of 'the full text survived'."""
        pattern = r'[\s\S]*?'.join(re.escape(w) for w in phrase.split())
        return re.search(pattern, text) is not None

    @staticmethod
    def _row_line(page_text, needle):
        """The single extracted text line containing `needle` (one table row
        extracts onto one text line while the row fits without wrapping)."""
        for line in page_text.split('\n'):
            if needle in line:
                return line
        return None

    # ------------------------------------------------------------------
    # 1. Line order — creation sequence preserved, excess LAST
    # ------------------------------------------------------------------

    def test_lines_print_in_creation_order_excess_last(self):
        po = self._make_po([
            {'description': 'Front bumper assembly',        'unit_price': 6000, 'vat': True},
            {'description': 'Radiator grille',              'unit_price': 4000, 'vat': True},
            {'description': 'Panel beating labour',         'unit_price': 5000, 'vat': True},
            {'description': 'Parts markup recovery',        'unit_price': 670,  'vat': True},
            {'description': 'Sundries and consumables',     'unit_price': 91,   'vat': True},
            {'description': 'Excess recovered from insured', 'unit_price': -3000},
        ])
        joined = '\n'.join(self._pages(po))
        order = [
            'Front bumper assembly',
            'Radiator grille',
            'Panel beating labour',
            'Parts markup recovery',
            'Sundries and consumables',
            'Excess recovered from insured',
        ]
        positions = []
        for desc in order:
            self.assertIn(desc, joined, f'line missing from print: {desc}')
            positions.append(joined.index(desc))
        self.assertEqual(positions, sorted(positions),
                         'lines did not print in creation order')
        # The regression that started all this: the excess must never float
        # above the work it deducts from.
        self.assertGreater(joined.index('Excess recovered'),
                           joined.index('Sundries and consumables'))
        self.assertGreater(joined.index('Excess recovered'),
                           joined.index('Front bumper assembly'))

    # ------------------------------------------------------------------
    # 1a2. Two-page contract — page 1 order only, page 2 fine-print terms
    # ------------------------------------------------------------------

    def test_two_page_layout_order_then_terms(self):
        # CFO directive 2026-07-08: a normal PO is EXACTLY two pages —
        # page 1 the clean order, page 2 all T&Cs in fine print + the QR.
        po = self._make_po([
            {'description': 'Front bumper assembly', 'unit_price': 6000, 'vat': True},
            {'description': 'Panel beating labour',  'unit_price': 5000, 'vat': True},
            {'description': 'Excess recovered',      'unit_price': -3000},
        ])
        pages = self._pages(po)
        self.assertEqual(len(pages), 2, 'normal PO must be exactly 2 pages')
        # Page 1: order only — no terms.
        self.assertIn('Subtotal (excl. VAT)', pages[0])
        self.assertNotIn('TERMS AND CONDITIONS', pages[0])
        self.assertNotIn('Workmanship Guarantee', pages[0])
        # Page 2: heading, terms, the explicit 30-days-from-statement
        # condition, the protective legal boilerplate, and the QR caption.
        self.assertIn('TERMS AND CONDITIONS', pages[1])
        self.assertIn('Workmanship Guarantee', pages[1])
        self.assertTrue(self._tokens_in_order(
            pages[1], '30 days from the statement date'))
        # CFO 2026-07-08: the PO is a legal document — the without-prejudice
        # / no-admission clause and governing law must always print.
        self.assertTrue(self._tokens_in_order(
            pages[1], 'without-prejudice basis'))
        self.assertTrue(self._tokens_in_order(
            pages[1], 'not an admission of liability'))
        self.assertTrue(self._tokens_in_order(
            pages[1], 'laws of the Republic of Botswana'))
        self.assertTrue(self._tokens_in_order(
            pages[1], 'Scan the QR code for acceptance or authentication.'))

    # ------------------------------------------------------------------
    # 1a3. Discount rows — gross subtotal + discount line when discounted
    # ------------------------------------------------------------------

    def test_discount_po_prints_gross_and_discount_rows(self):
        po = self._make_po(
            [{'description': 'Event package', 'unit_price': 1000, 'vat': True}],
            discount_percent=Decimal('5.00'),
        )
        page1 = self._pages(po)[0]
        self.assertTrue(self._tokens_in_order(page1, 'Subtotal (gross)'))
        self.assertTrue(self._tokens_in_order(page1, 'Discount (5%)'))
        self.assertIn(_p('1000'), page1)          # gross
        self.assertIn(_p('-50'), page1)           # money off
        self.assertIn(_p('950'), page1)           # net subtotal
        self.assertIn(_p('1083'), page1)          # 950 + 14% VAT

    def test_undiscounted_po_has_no_discount_rows(self):
        po = self._make_po(
            [{'description': 'Repair work', 'unit_price': 1000, 'vat': True}])
        page1 = self._pages(po)[0]
        self.assertNotIn('Discount (', page1)
        self.assertNotIn('Subtotal (gross)', page1)

    # ------------------------------------------------------------------
    # 1b. QR verification block — caption + this PO's unique verify link
    # ------------------------------------------------------------------

    def test_qr_verification_block_present(self):
        po = self._make_po([
            {'description': 'Panel beating labour', 'unit_price': 5000, 'vat': True},
        ])
        joined = '\n'.join(self._pages(po))
        self.assertTrue(
            self._tokens_in_order(
                joined, 'Scan the QR code for acceptance or authentication.'),
            'QR caption missing from PO print',
        )
        # The printed fallback link must carry THIS PO's id — each PO's QR is
        # unique, and the id is the unguessable capability token.
        self.assertIn(str(po.pk), joined, 'verify link missing this PO id')
        self.assertTrue(self._tokens_in_order(joined, '/api/verify/po/'),
                        'verify path missing from print')

    # ------------------------------------------------------------------
    # 2. Taxes column semantics — real rate or '—', never a blanket label
    # ------------------------------------------------------------------

    def test_no_vat_line_shows_dash_and_vat_line_shows_rate(self):
        po = self._make_po([
            {'description': 'Respray labour',          'unit_price': 2000, 'vat': True},
            {'description': 'Policy excess deduction', 'unit_price': -500},
        ])
        page1 = self._pages(po)[0]

        vat_row = self._row_line(page1, 'Respray labour')
        self.assertIsNotNone(vat_row, 'VAT line row not found')
        # Letterhead layout: dedicated VAT column, cell shows the rate only.
        self.assertIn('14%', vat_row)

        excess_row = self._row_line(page1, 'Policy excess deduction')
        self.assertIsNotNone(excess_row, 'excess row not found')
        self.assertNotIn('VAT', excess_row,
                         'no-VAT line must not carry a VAT label')
        self.assertIn('—', excess_row,      # '—'
                      "no-VAT line should show '—' in the Taxes column")

    # ------------------------------------------------------------------
    # 3. Long description wraps — text survives, other columns intact
    # ------------------------------------------------------------------

    def test_long_description_wraps_and_columns_survive(self):
        desc = ('Supply and fit genuine manufacturer front bumper assembly '
                'including all brackets clips fasteners and colour coded '
                'paintwork finish to spec')
        self.assertGreaterEqual(len(desc), 120)
        po = self._make_po([
            {'description': desc, 'unit_price': Decimal('12345.67'), 'vat': True},
        ])
        pages = self._pages(po)
        self.assertGreaterEqual(len(pages), 1)
        joined = '\n'.join(pages)
        # The wrapped Paragraph breaks at spaces (no hyphenation) and its
        # continuation lines interleave with the neighbour columns in the
        # extraction — every word must survive, in order.
        self.assertTrue(self._tokens_in_order(joined, desc),
                        'full long description did not survive wrapping')
        # Every other column of that row still prints.
        self.assertIn('14%', joined)                       # VAT column (rate)
        self.assertIn('12,345.67', joined)                 # unit price
        self.assertIn(_p('12345.67'), joined)              # amount
        self.assertIn('01 July 2026', joined)              # order date

    # ------------------------------------------------------------------
    # 4. Special characters — & < > survive EVERYWHERE, nothing eaten
    # ------------------------------------------------------------------

    def test_special_characters_survive_in_all_dynamic_text(self):
        # reportlab's Paragraph silently SWALLOWS unescaped '<...>' (it parses
        # as markup) — these strings must reach the page verbatim.
        supplier = Contact.objects.create(
            name='B&B Panel <T/A> Beaters', contact_type='vendor',
            company=self.company,
        )
        po = self._make_po(
            [{'description': 'Bumper & bracket <steel> load > 50kg',
              'unit_price': 1000, 'vat': True}],
            supplier=supplier,
            related_claim_reference='G2026<X>&7',
            justification='Approved by Smith & Sons <assessor> for repair',
        )
        joined = '\n'.join(self._pages(po))
        # The description cell may wrap — assert token survival in order
        # (reportlab EATS '<steel>' entirely when unescaped, so presence of
        # every token IS the regression check).
        self.assertTrue(
            self._tokens_in_order(joined, 'Bumper & bracket <steel> load > 50kg'),
            'special characters were eaten from the line description')
        self.assertIn('B&B Panel <T/A> Beaters', joined)
        self.assertIn('G2026<X>&7', joined)
        self.assertIn('Smith & Sons <assessor>', joined)

    # ------------------------------------------------------------------
    # 5. Multi-page — header row repeats, totals after the last line
    # ------------------------------------------------------------------

    def test_forty_lines_multipage_header_repeats_totals_on_final(self):
        specs = [{'description': f'Line item {i:02d} replacement part',
                  'unit_price': 100 + i, 'vat': True} for i in range(40)]
        po = self._make_po(specs)
        pages = self._pages(po)
        self.assertGreaterEqual(len(pages), 2, 'expected a multi-page PO')
        # repeatRows=1 — the continuation page reprints the column headers.
        self.assertIn('UNIT PRICE', pages[1],
                      'lines-table header did not repeat on page 2')
        # Every line survived pagination.
        joined = '\n'.join(pages)
        for i in (0, 20, 39):
            self.assertIn(f'Line item {i:02d} replacement part', joined)
        # Totals come after the last line, never on page 1.
        totals_pages = [i for i, t in enumerate(pages) if 'Subtotal (excl. VAT)' in t]
        self.assertEqual(len(totals_pages), 1, 'totals block must print once')
        self.assertGreaterEqual(totals_pages[0], 1)
        self.assertIn(_p(po.total_amount), pages[totals_pages[0]])
        self.assertLess(joined.index('Line item 39'),
                        joined.index('Subtotal (excl. VAT)'))

    # ------------------------------------------------------------------
    # 6. Totals correctness — stored header totals, P-prefixed
    # ------------------------------------------------------------------

    def test_totals_block_prints_stored_po_totals(self):
        po = self._make_po([
            {'description': 'Panel beating labour', 'unit_price': 5000, 'vat': True},
            {'description': 'Paintwork',            'unit_price': 2000, 'vat': True},
            {'description': 'Excess deduction',     'unit_price': -3000},
        ])
        # Sanity: the model math this print must agree with.
        self.assertEqual(po.subtotal,     Decimal('4000.00'))
        self.assertEqual(po.tax_total,    Decimal('980.00'))
        self.assertEqual(po.total_amount, Decimal('4980.00'))

        joined = '\n'.join(self._pages(po))
        self.assertIn('Subtotal (excl. VAT)', joined)
        self.assertIn(_p('4000'), joined)
        self.assertIn('TOTAL', joined)
        self.assertIn(_p('980'), joined)
        self.assertIn(_p('4980'), joined)
        self.assertIn(_p('-3000'), joined)      # the excess line's amount

    def test_no_vat_po_never_fabricates_vat(self):
        # A PO whose lines carry NO VAT must print its stored zero tax —
        # not a derived 14% that contradicts po.total_amount.
        po = self._make_po([
            {'description': 'Zero rated service A', 'unit_price': 1000},
            {'description': 'Zero rated service B', 'unit_price': 500},
        ], department='admin')
        self.assertEqual(po.tax_total, Decimal('0.00'))
        self.assertEqual(po.total_amount, Decimal('1500.00'))

        joined = '\n'.join(self._pages(po))
        self.assertIn(_p('1500'), joined)               # subtotal AND total
        self.assertIn(_p('0'), joined)                  # VAT P0.00
        self.assertNotIn('210.00', joined)              # 14% of 1,500 — fabricated
        self.assertNotIn('1,710.00', joined)            # 1,500 + fake VAT

    # ------------------------------------------------------------------
    # 7. Degenerate POs — no lines / negative-only line
    # ------------------------------------------------------------------

    def test_po_with_no_lines_renders_placeholder(self):
        po = self._make_po([])
        joined = '\n'.join(self._pages(po))
        self.assertIn('(no lines on this order)', joined)
        self.assertIn('Subtotal (excl. VAT)', joined)

    def test_negative_only_line_renders_without_fake_negative_vat(self):
        po = self._make_po([
            {'description': 'Credit adjustment on prior order',
             'unit_price': -3000},
        ], department='admin')
        self.assertEqual(po.total_amount, Decimal('-3000.00'))
        joined = '\n'.join(self._pages(po))
        self.assertIn(_p('-3000'), joined)
        # The old fallback derived VAT -P420.00 and Total -P3,420.00 here.
        self.assertNotIn('420.00', joined)
        self.assertNotIn('3,420.00', joined)

    # ------------------------------------------------------------------
    # 8. Department gating — repair T&Cs on claims POs only
    # ------------------------------------------------------------------

    def test_repair_terms_only_on_claims_pos(self):
        claims_po = self._make_po(
            [{'description': 'Panel beating', 'unit_price': 1000, 'vat': True}],
            department='claims',
        )
        admin_po = self._make_po(
            [{'description': 'Office stationery', 'unit_price': 200, 'vat': True}],
            department='admin',
        )
        claims_text = '\n'.join(self._pages(claims_po))
        admin_text  = '\n'.join(self._pages(admin_po))

        self.assertIn('Terms and Conditions for Repair Orders', claims_text)
        self.assertIn('Workmanship Guarantee', claims_text)
        self.assertNotIn('Terms and Conditions for Repair Orders', admin_text)
        self.assertNotIn('Workmanship Guarantee', admin_text)
        # Every PO still carries the invoice + dispute conditions.
        for text in (claims_text, admin_text):
            self.assertIn('Invoice Payment Conditions', text)
            self.assertIn('Dispute Resolution', text)
