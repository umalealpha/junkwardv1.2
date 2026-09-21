"""
The monthly Premium / Claims / Loss Ratio report.

These tests pin the translations, not the plumbing. Every failure mode listed in
the finance team's own walkthrough gets one: the products that come from a
different report on the premium side than on the claims side, the two products
that fold into one regulatory row, Third Party Car counting as Motor in Table 3
while sitting inside Instant Insurance in Table 1, and the tie-back that catches
all of it when someone gets it wrong anyway.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from finance_report.engine import (
    ClaimRow, CORPORATE_LINES, HOSPITAL_CASHBACK_AND_LEGAL, INSTANT_INSURANCE,
    MOTOR, MOTOR_COMPREHENSIVE, NON_MOTOR, PAID, PERSONAL_LINES, PremiumRow,
    RESERVE, UNION_LEGAL, build_report, claim_amount, claims_table_1,
    claims_table_4, loss_ratio_table, premium_table_1, premium_table_2,
    premium_table_3, premium_table_4, reconcile, regulatory_for_mom_product,
)
from finance_report.parsers import (
    SourceColumnMissing, manual_premium_rows, parse_claims_as_on_date,
    parse_month_on_month, parse_premium_board,
)


def P(month, line, regulatory, amount, product=''):
    return PremiumRow(month=month, line=line, regulatory=regulatory,
                      amount=Decimal(str(amount)), product=product)


def C(month, line, regulatory, reserve, claim_type='', paid=0):
    return ClaimRow(month=month, line=line, regulatory=regulatory,
                    claim_type=claim_type, reserve=Decimal(str(reserve)),
                    paid=Decimal(str(paid)))


class PremiumTableTests(SimpleTestCase):

    def setUp(self):
        self.rows = [
            P('2026-07', CORPORATE_LINES, 'Motor', 1000),
            P('2026-07', CORPORATE_LINES, 'Property', 400),
            P('2026-07', PERSONAL_LINES, 'Motor', 600),
            P('2026-08', PERSONAL_LINES, 'Accident', 200),
            P('2026-07', INSTANT_INSURANCE, 'Motor', 300, product='Third Party Car Insurance'),
            P('2026-07', INSTANT_INSURANCE, 'Accident', 100, product='Accidental Death Insurance'),
            P('2026-08', MOTOR_COMPREHENSIVE, 'Motor', 500, product='Motor Comprehensive'),
        ]

    def test_ytd_keeps_earlier_months_in_the_total(self):
        t = premium_table_1(self.rows)
        self.assertEqual(t.months, ['2026-07', '2026-08'])
        # Personal Lines: 600 in July, 200 in August. YTD is both, not the latest.
        self.assertEqual(t.ytd(PERSONAL_LINES), Decimal('800.00'))
        self.assertEqual(t.total_ytd(), Decimal('3100.00'))

    def test_named_lines_survive_a_month_with_no_figures(self):
        """Union Legal had nothing this period. A row that disappears reads as
        'not in this report' rather than 'zero', and someone goes looking."""
        t = premium_table_1(self.rows)
        self.assertIn(UNION_LEGAL, t.rows)
        self.assertEqual(t.ytd(UNION_LEGAL), Decimal('0.00'))

    def test_table_2_splits_only_corporate_and_personal(self):
        t = premium_table_2(self.rows)
        self.assertEqual(t.ytd(f'{CORPORATE_LINES} {MOTOR}'), Decimal('1000.00'))
        self.assertEqual(t.ytd(f'{CORPORATE_LINES} {NON_MOTOR}'), Decimal('400.00'))
        # Instant Insurance carries over whole — it is not split here.
        self.assertEqual(t.ytd(INSTANT_INSURANCE), Decimal('400.00'))
        self.assertNotIn(f'{INSTANT_INSURANCE} {MOTOR}', t.rows)

    def test_table_3_pulls_third_party_car_out_of_instant_insurance(self):
        """Third Party Car sits inside Instant Insurance in Table 1 and counts as
        Motor here. Miss it and Motor is understated while the total still ties,
        so nothing flags the error."""
        t = premium_table_3(self.rows)
        # 1000 corporate motor + 600 personal motor + 500 motor comp + 300 TPC
        self.assertEqual(t.ytd(MOTOR), Decimal('2400.00'))
        self.assertEqual(t.ytd(NON_MOTOR), Decimal('700.00'))

    def test_all_four_premium_tables_tie(self):
        tables = [premium_table_1(self.rows), premium_table_2(self.rows),
                  premium_table_3(self.rows), premium_table_4(self.rows)]
        rec = reconcile(tables)
        self.assertTrue(rec['balanced'], rec)
        self.assertEqual(rec['difference'], '0.00')

    def test_reconcile_reports_a_break_instead_of_hiding_it(self):
        good = premium_table_1(self.rows)
        broken = premium_table_4(self.rows)
        broken.add('Motor', '2026-07', Decimal('50'))   # a product mapped twice
        rec = reconcile([good, broken])
        self.assertFalse(rec['balanced'])
        self.assertEqual(rec['difference'], '50.00')
        self.assertIn('Month-on-Month', rec['note'])


class RegulatoryTranslationTests(SimpleTestCase):

    def test_two_products_fold_into_one_regulatory_row(self):
        self.assertEqual(regulatory_for_mom_product('Hospital Cashback Insurance'),
                         HOSPITAL_CASHBACK_AND_LEGAL)
        self.assertEqual(regulatory_for_mom_product('Legal Insurance'),
                         HOSPITAL_CASHBACK_AND_LEGAL)

    def test_combined_row_is_added_not_kept_separate(self):
        rows = [P('2026-07', INSTANT_INSURANCE, HOSPITAL_CASHBACK_AND_LEGAL, 100),
                P('2026-07', INSTANT_INSURANCE, HOSPITAL_CASHBACK_AND_LEGAL, 250)]
        self.assertEqual(premium_table_4(rows).ytd(HOSPITAL_CASHBACK_AND_LEGAL),
                         Decimal('350.00'))

    def test_an_unmapped_product_keeps_its_own_name(self):
        """A new product nobody has mapped must be visible, not swept into
        Miscellaneous where it would never be noticed."""
        self.assertEqual(regulatory_for_mom_product('Pet Insurance'), 'Pet Insurance')


class ClaimsTableTests(SimpleTestCase):

    def test_hospital_cashback_and_legal_is_built_from_claim_type(self):
        """The claims report's regulatory mapping has no such row at all. Built
        from Claim Type so it matches the premium tab's row name — otherwise the
        loss-ratio tab divides a premium by nothing."""
        rows = [
            C('2026-07', PERSONAL_LINES, 'Accident', -100, claim_type='Hospital Cashback'),
            C('2026-07', PERSONAL_LINES, 'Accident', -50, claim_type='Legal Insurance'),
            C('2026-07', CORPORATE_LINES, 'Motor', -900, claim_type='Accident Damage'),
        ]
        t = claims_table_4(rows)
        self.assertEqual(t.ytd(HOSPITAL_CASHBACK_AND_LEGAL), Decimal('-150.00'))
        self.assertEqual(t.ytd('Motor'), Decimal('-900.00'))
        self.assertNotIn('Accident', t.rows)

    def test_negative_reserves_are_left_alone(self):
        rows = [C('2026-07', CORPORATE_LINES, 'Motor', -1500)]
        self.assertEqual(claims_table_1(rows).ytd(CORPORATE_LINES), Decimal('-1500.00'))


class LossRatioTests(SimpleTestCase):

    def test_ratio_sign_follows_the_reserves(self):
        prem = premium_table_1([P('2026-07', CORPORATE_LINES, 'Motor', 1000)])
        rows = [C('2026-07', CORPORATE_LINES, 'Motor', -673)]
        res, pd = claims_table_1(rows, RESERVE), claims_table_1(rows, PAID)
        row = [r for r in loss_ratio_table('x', prem, res, pd)['rows']
               if r['name'] == CORPORATE_LINES][0]
        self.assertEqual(row['loss_ratio_pct'], '-67.30')
        self.assertEqual(row['pct_of_premium'], '100.00')

    def test_paid_and_reserve_are_reported_separately_and_incurred_is_their_sum(self):
        """The CFO wants both sides analysed on their own. A row shows paid,
        reserve, incurred (paid+reserve) and a loss ratio on paid and incurred."""
        prem = premium_table_1([P('2026-07', CORPORATE_LINES, 'Motor', 1000)])
        rows = [C('2026-07', CORPORATE_LINES, 'Motor', 200, paid=300)]
        res, pd = claims_table_1(rows, RESERVE), claims_table_1(rows, PAID)
        row = [r for r in loss_ratio_table('x', prem, res, pd)['rows']
               if r['name'] == CORPORATE_LINES][0]
        self.assertEqual(row['paid_ytd'], '300.00')
        self.assertEqual(row['reserve_ytd'], '200.00')
        self.assertEqual(row['incurred_ytd'], '500.00')
        self.assertEqual(row['loss_ratio_paid_pct'], '30.00')     # 300/1000
        self.assertEqual(row['loss_ratio_pct'], '50.00')          # incurred 500/1000

    def test_claim_amount_selects_the_measure(self):
        r = C('2026-07', CORPORATE_LINES, 'Motor', 200, paid=300)
        self.assertEqual(claim_amount(r, RESERVE), Decimal('200.00'))
        self.assertEqual(claim_amount(r, PAID), Decimal('300.00'))

    def test_a_row_running_hot_is_flagged_against_the_books_own_ratio(self):
        prem = premium_table_1([
            P('2026-07', CORPORATE_LINES, 'Motor', 1000),
            P('2026-07', MOTOR_COMPREHENSIVE, 'Motor', 1000, product='Motor Comprehensive'),
        ])
        rows = [C('2026-07', CORPORATE_LINES, 'Motor', -200),
                C('2026-07', MOTOR_COMPREHENSIVE, 'Motor', -1400)]
        res, pd = claims_table_1(rows, RESERVE), claims_table_1(rows, PAID)
        by_name = {r['name']: r for r in loss_ratio_table('x', prem, res, pd)['rows']}
        self.assertEqual(by_name[MOTOR_COMPREHENSIVE]['status'], 'High')
        self.assertEqual(by_name[CORPORATE_LINES]['status'], 'Good')

    def test_a_line_with_claims_but_no_premium_does_not_divide_by_zero(self):
        prem = premium_table_1([P('2026-07', CORPORATE_LINES, 'Motor', 1000)])
        rows = [C('2026-07', PERSONAL_LINES, 'Motor', -50)]
        res, pd = claims_table_1(rows, RESERVE), claims_table_1(rows, PAID)
        by_name = {r['name']: r for r in loss_ratio_table('x', prem, res, pd)['rows']}
        self.assertIsNone(by_name[PERSONAL_LINES]['loss_ratio_pct'])
        self.assertEqual(by_name[PERSONAL_LINES]['status'], 'No premium')


class ParserTests(SimpleTestCase):

    PREMIUM_HEADER = ['Policy No.', 'Booking Date', 'Total Premium (BWP incl VAT)',
                      'Total Premium (BWP excl VAT)', 'Regulatory Class (NBFIRA)']

    def test_premium_board_uses_booking_date_and_the_ex_vat_column(self):
        rows = [
            ['COMG2024099515', '2026-07-15 00:00:00', '114.00', '100.00', 'Motor'],
            ['DOMG2025000001', '2026-08-02 00:00:00', '228.00', '200.00', 'Accident'],
        ]
        out, skipped = parse_premium_board(self.PREMIUM_HEADER, rows)
        self.assertEqual([r.month for r in out], ['2026-07', '2026-08'])
        self.assertEqual([r.line for r in out], [CORPORATE_LINES, PERSONAL_LINES])
        self.assertEqual(out[0].amount, Decimal('100.00'))
        self.assertEqual(skipped['no_line'], 0)

    def test_premium_board_backs_vat_out_when_the_ex_vat_column_is_gone(self):
        header = ['Policy No.', 'Booking Date', 'Total Premium (BWP incl VAT)',
                  'Regulatory Class (NBFIRA)']
        out, _ = parse_premium_board(header, [['COMG1', '2026-07-15', '114.00', 'Motor']])
        self.assertEqual(out[0].amount, Decimal('100.00'))

    def test_a_policy_with_no_known_prefix_is_counted_not_silently_dropped(self):
        out, skipped = parse_premium_board(
            self.PREMIUM_HEADER, [['XXXX1', '2026-07-15', '114.00', '100.00', 'Motor']])
        self.assertEqual(out, [])
        self.assertEqual(skipped['no_line'], 1)

    def test_missing_column_raises_rather_than_totalling_zero(self):
        with self.assertRaises(SourceColumnMissing):
            parse_premium_board(['Policy No.', 'Total Premium (BWP excl VAT)'], [])

    def test_month_on_month_splits_on_product_type_and_keeps_the_product(self):
        header = ['POLICY NUMBER', 'Amount Collected (P)', 'Amount Collected Exc Vat',
                  'Product', 'Product type', 'Paygate', 'Year/Date']
        rows = [
            ['MIS1', '49', '42.98', 'Third Party Car Insurance', 'Instant Insurance',
             'VCS', '2026-07-01 02:00:00'],
            ['MIS2', '600', '526.32', 'Motor Comprehensive', 'Motor Comprehensive',
             'VCS', '2026-07-04 02:00:00'],
        ]
        out, _ = parse_month_on_month(header, rows)
        self.assertEqual([r.line for r in out], [INSTANT_INSURANCE, MOTOR_COMPREHENSIVE])
        self.assertEqual(out[0].product, 'Third Party Car Insurance')
        self.assertEqual(out[0].regulatory, MOTOR)
        self.assertEqual(out[0].amount, Decimal('42.98'))

    def test_claims_mis_prefix_becomes_instant_or_motor_comp(self):
        header = ['claim_number', 'policyNumber', 'claimType', 'productName',
                  'reportedDate', 'reserveAmt', 'regulatoryMapping']
        rows = [
            ['G1', 'MIS2021023579', 'Glass', 'Third Party Car Insurance',
             '2026-07-11 00:00:00', '-500', 'Motor'],
            ['G2', 'MIS2020007130', 'Accident Damage', 'Motor Comprehensive',
             '2026-07-12 00:00:00', '-9000', 'Motor'],
            ['G3', 'COMG2024127478', 'Glass', 'Commercial Insurance',
             '2026-08-01 00:00:00', '-46262', 'Motor'],
        ]
        out, skipped = parse_claims_as_on_date(header, rows)
        self.assertEqual([r.line for r in out],
                         [INSTANT_INSURANCE, MOTOR_COMPREHENSIVE, CORPORATE_LINES])
        self.assertEqual(out[2].month, '2026-08')
        self.assertEqual(skipped['no_line'], 0)

    def test_xlsb_serial_dates_are_read_as_dates_not_discarded(self):
        """The .xlsb claims export hands dates back as Excel serial numbers.
        Read as text they parse as nothing, every claim row drops out, and the
        premium side still totals perfectly — so the report looks finished and
        shows a zero loss ratio. This is the bug the real files caught."""
        header = ['policyNumber', 'claimType', 'productName', 'reportedDate',
                  'reserveAmt', 'regulatoryMapping']
        # 46262 = 2026-08-05; also accept it as the string some readers emit.
        rows = [['COMG1', 'Glass', 'Commercial', 46262.0, '-500', 'Motor'],
                ['COMG2', 'Glass', 'Commercial', '46262.0', '-500', 'Motor']]
        out, skipped = parse_claims_as_on_date(header, rows)
        self.assertEqual(skipped['no_month'], 0)
        self.assertEqual([r.month for r in out], ['2026-08', '2026-08'])

    def test_a_plain_amount_in_a_date_column_is_not_read_as_a_date(self):
        """46262 is a date; 500 is not. Without the range check a reserve that
        strays into a date column would be booked to the year 1901."""
        header = ['policyNumber', 'claimType', 'productName', 'reportedDate',
                  'reserveAmt', 'regulatoryMapping']
        out, skipped = parse_claims_as_on_date(
            header, [['COMG1', 'Glass', 'Commercial', 500, '-500', 'Motor']])
        self.assertEqual(out, [])
        self.assertEqual(skipped['no_month'], 1)

    def test_an_empty_cell_does_not_become_a_category_called_None(self):
        """openpyxl and pyxlsb return None for a blank cell, and str(None) is
        the word "None". Left alone it becomes a regulatory category named None
        carrying a real total, while every table still ties so nothing flags."""
        header = ['policyNumber', 'claimType', 'productName', 'reportedDate',
                  'reserveAmt', 'regulatoryMapping']
        out, _ = parse_claims_as_on_date(
            header, [['COMG1', None, None, '2026-07-11', '-500', None]])
        self.assertEqual(out[0].regulatory, '')
        self.assertEqual(out[0].claim_type, '')

    def test_manual_lines_are_restricted_to_the_two_that_have_no_report(self):
        out = manual_premium_rows([{'month': '2026-07', 'line': UNION_LEGAL, 'amount': '1234.56'}])
        self.assertEqual(out[0].amount, Decimal('1234.56'))
        with self.assertRaises(ValueError):
            manual_premium_rows([{'month': '2026-07', 'line': CORPORATE_LINES, 'amount': '1'}])


class WholeReportTests(SimpleTestCase):

    def test_claims_outside_the_period_are_excluded_and_counted(self):
        """Claims As On Date is inception-to-date. Unfiltered, all-time reserves
        divided by one period of premium produced a 104% loss ratio on the real
        files — a book that looks like it is losing money and is not."""
        prem = [P('2026-07', CORPORATE_LINES, 'Motor', 1000)]
        clm = [C('2026-07', CORPORATE_LINES, 'Motor', -300),
               C('2019-03', CORPORATE_LINES, 'Motor', -9000)]   # long before the period
        rep = build_report(prem, clm)

        self.assertEqual(rep['period']['claims_rows_outside_period'], 1)
        self.assertIn('inception-to-date', rep['period']['note'])
        self.assertEqual(rep['loss_ratio'][0]['total']['loss_ratio_pct'], '-30.00')

    def test_an_explicit_period_wins_over_the_premium_months(self):
        prem = [P('2026-07', CORPORATE_LINES, 'Motor', 1000),
                P('2026-08', CORPORATE_LINES, 'Motor', 1000)]
        clm = [C('2026-07', CORPORATE_LINES, 'Motor', -500),
               C('2026-08', CORPORATE_LINES, 'Motor', -500)]
        rep = build_report(prem, clm, months=['2026-07'])
        self.assertEqual(rep['months'], ['2026-07'])
        self.assertEqual(rep['period']['premium_rows_outside_period'], 1)
        self.assertEqual(rep['loss_ratio'][0]['total']['premium_ytd'], '1000.00')

    def test_build_report_returns_three_tabs_that_tie(self):
        prem = [P('2026-07', CORPORATE_LINES, 'Motor', 1000),
                P('2026-07', PERSONAL_LINES, 'Accident', 500)]
        clm = [C('2026-07', CORPORATE_LINES, 'Motor', -400, claim_type='Accident Damage')]
        rep = build_report(prem, clm)

        self.assertEqual(len(rep['premium']['tables']), 4)
        self.assertEqual(len(rep['claims']['reserve']['tables']), 4)
        self.assertEqual(len(rep['claims']['paid']['tables']), 4)
        self.assertEqual(len(rep['loss_ratio']), 3)
        self.assertTrue(rep['premium']['reconciliation']['balanced'])
        self.assertTrue(rep['claims']['reserve']['reconciliation']['balanced'])
        self.assertTrue(rep['claims']['paid']['reconciliation']['balanced'])
        # incurred: reserve -400 + paid 0 = -400 over 1500 premium
        self.assertEqual(rep['loss_ratio'][0]['total']['loss_ratio_pct'], '-26.67')
        self.assertEqual(rep['months'], ['2026-07'])
