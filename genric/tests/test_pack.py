"""WHY: the spine. One button must produce a manifest that says what is in the
pack AND what is not, and never quietly ships a report it could not build.

Also pins the DPA rule — policy numbers only — at the loader, because a name
that never enters the process cannot leave it.
"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from genric import policies as P
from genric import reports as R
from genric.services import build_pack


class _Line:
    def __init__(self, description, amount, day=1):
        self.id = f'{description}{amount}{day}'
        self.description = description
        self.amount = Decimal(str(amount))
        self.reference = ''
        self.transaction_date = date(2026, 7, day)


_CSV = (
    'Policy Number,Client Name,ID Number,Payment Status,createdAt,Premium,Cell\n'
    'POL0001,Thabo Nkosi,8001015009087,Payment Successful,2026-07-03,99.00,0721234567\n'
    'POL0002,Lerato Dube,9002025009087,Payment Failed,2025-01-10,99.00,0729876543\n'
    'POL0003,Sipho Khumalo,7503035009087,Payment Status Not Found,2026-07-20,99.00,0731112222\n'
)


class PolicyLoaderDpaTests(SimpleTestCase):

    def test_only_the_policy_number_survives_the_load(self):
        rows = P.load_export(_CSV)
        self.assertEqual(len(rows), 3)
        blob = repr(rows)
        for pii in ('Thabo', 'Nkosi', 'Lerato', 'Sipho',
                    '8001015009087', '0721234567'):
            self.assertNotIn(pii, blob, f'{pii} reached a PolicyRow')

    def test_payment_statuses_map_to_the_three_graphite_states(self):
        rows = {r.policy_number: r.status for r in P.load_export(_CSV)}
        self.assertEqual(rows['POL0001'], P.PAID)
        self.assertEqual(rows['POL0002'], P.FAILED)
        self.assertEqual(rows['POL0003'], P.DORMANT)

    def test_inception_dates_are_parsed(self):
        rows = {r.policy_number: r.created_at for r in P.load_export(_CSV)}
        self.assertEqual(rows['POL0001'], date(2026, 7, 3))

    def test_columns_are_matched_by_name_never_by_position(self):
        """A Graphite export that reorders its columns must still load."""
        reordered = (
            'Premium,createdAt,Payment Status,Policy Number\n'
            '99.00,2026-07-03,Payment Successful,POL0001\n'
        )
        row = P.load_export(reordered)[0]
        self.assertEqual(row.policy_number, 'POL0001')
        self.assertEqual(row.status, P.PAID)


class PackSpineTests(TestCase):

    def _build(self):
        lines = [_Line('REREALPAY COLLECTION', 99, day=(i % 28) + 1) for i in range(93)]
        lines += [_Line('#SERVICE FEES', -350)]
        return build_pack(2026, 7, bank_lines=lines,
                          current_policies=P.load_export(_CSV),
                          policy_source='graphite_export',
                          explicit_invoice_number='GENRIC-1-024')

    def test_thirteen_reports_come_back(self):
        _, built = self._build()
        self.assertEqual(len(built), 13)

    def test_the_master_is_built_last_and_is_present(self):
        _, built = self._build()
        self.assertEqual(built[-1].key, 'master')

    def test_no_report_is_missing_a_status(self):
        _, built = self._build()
        for rep in built:
            self.assertIn(rep.status,
                          (R.OK, R.NIL_NO_ACTIVITY, R.NIL_NO_SOURCE, R.BLOCKED),
                          rep.key)

    def test_gwp_ties_to_the_july_worked_example(self):
        ctx, built = self._build()
        self.assertEqual(ctx.summary.confirmed_gwp_incl_vat, Decimal('9207.00'))
        self.assertEqual(ctx.cession.net_reinsurance_premium_due, Decimal('5656.30'))

    def test_reports_with_no_source_are_not_dressed_up_as_nil_returns(self):
        """A nil return means 'we looked and there was nothing'. These say
        'there is nothing to look at', which is a different sentence."""
        _, built = self._build()
        no_source = [r for r in built if r.status == R.NIL_NO_SOURCE]
        self.assertTrue(no_source)
        for rep in no_source:
            self.assertNotEqual(rep.status, R.NIL_NO_ACTIVITY)
            self.assertIn('NOT a nil return', ' '.join(rep.notes))

    def test_the_master_lists_the_open_items(self):
        _, built = self._build()
        master = built[-1]
        opens = next(n for n in master.notes if n.startswith('OPEN ITEMS'))
        self.assertIn('Unproduced for want of a source', opens)

    def test_the_master_no_longer_asks_the_pay_window_question(self):
        """The CFO answered it on 14 Sep 2026 — 30 days. It is not open."""
        _, built = self._build()
        opens = next(n for n in built[-1].notes if n.startswith('OPEN ITEMS'))
        self.assertNotIn('how many days', opens.lower())

    def test_the_master_DOES_still_ask_for_genrics_bank_details(self):
        """The other question is NOT answered and must not read as all clear.

        Before this it was never listed here at all — the loop walks the
        reports and the invoice is not one of them — so with the pay window
        answered the control sheet would have shown a clean OPEN ITEMS while
        the invoice still could not say where to pay.
        """
        _, built = self._build()
        opens = next(n for n in built[-1].notes if n.startswith('OPEN ITEMS'))
        self.assertIn('Which account does Alpha Direct pay', opens)
        self.assertIn('NOT ON FILE, DO NOT PAY AGAINST THIS DOCUMENT', opens)

    def test_the_master_says_how_many_policies_the_window_could_not_measure(self):
        """The three-row test export carries no failed-collection date."""
        _, built = self._build()
        opens = next(n for n in built[-1].notes if n.startswith('OPEN ITEMS'))
        self.assertIn('no failed-collection date', opens)
        self.assertIn('neither recommended nor cleared', opens)

    def test_the_master_states_that_nothing_was_posted(self):
        _, built = self._build()
        self.assertTrue(any('No journal was posted' in n for n in built[-1].notes))

    def test_the_bank_gap_against_graphite_is_reported_not_reconciled_away(self):
        ctx, built = self._build()
        # 93 in the bank, 1 "successful" in the three-row export.
        self.assertEqual(ctx.book.bank_vs_graphite_gap, 92)
        gwp = next(r for r in built if r.key == 'gwp')
        self.assertTrue(any('Gap of 92' in n for n in gwp.notes))

    def test_a_bad_month_is_refused(self):
        with self.assertRaises(ValueError):
            build_pack(2026, 13, bank_lines=[])


class ExportTests(TestCase):
    """Prove a file actually comes out, not just a dict."""

    def test_workbook_and_invoice_pdf_are_produced(self):
        from genric.exports import invoice_pdf, pack_workbook
        from genric.services import PackResult

        ctx, built = build_pack(
            2026, 7,
            bank_lines=[_Line('REREALPAY COLLECTION', 99, day=(i % 28) + 1)
                        for i in range(93)],
            current_policies=P.load_export(_CSV),
            explicit_invoice_number='GENRIC-1-024')
        result = PackResult(run=None, context=ctx, reports=built)

        xlsx = pack_workbook(result).getvalue()
        self.assertGreater(len(xlsx), 5000)
        self.assertTrue(xlsx.startswith(b'PK'))

        pdf = invoice_pdf(result)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_the_manifest_counts_what_was_and_was_not_produced(self):
        from genric.services import PackResult
        ctx, built = build_pack(
            2026, 7,
            bank_lines=[_Line('REREALPAY COLLECTION', 99)],
            current_policies=P.load_export(_CSV),
            explicit_invoice_number='GENRIC-1-024')
        m = PackResult(run=None, context=ctx, reports=built).manifest()
        self.assertEqual(len(m['part_a_regulatory_pack']), 13)
        self.assertGreaterEqual(m['not_produced'], 6)
        self.assertEqual(m['part_b_reinsurance_submission']['invoice']['vat'], '0.00')
        treaty = m['part_b_reinsurance_submission']['master_reinsurance']['treaty']
        self.assertEqual(treaty['ceding_commission'], '0.215')
        self.assertIn('the signed treaty wins', treaty['ceding_commission_note'])
