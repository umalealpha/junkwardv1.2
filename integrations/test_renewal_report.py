"""Tests for [B1], the renewal report.

Each test is one thing Finance asked for and one way the report could quietly be
wrong, written so it fails if the rule is dropped:

  * RENEW rows are the cron's billing cycles — 28,529 of them live against 2,529
    real anniversaries. Counting them puts a monthly policy on the list twelve
    times, which is the whole reason the report could not just read transactions.
  * The year is NOT a filter (Finance, 14-Sep). A policy that started in 2023
    still renews in October.
  * The in-force premium is what the customer pays per billing period, so a
    monthly policy is one twelfth of the annual figure — not the annual figure.
  * A frequency code Graphite does not explain, and a policy with no issued
    invoice, are REPORTED. Neither is dropped and neither is given a made-up
    value; code 6 alone carries 226 live domestic/commercial policies.

`graphite_ro.query` is faked. The fake is not a SQL engine — it returns the rows
the replica would return for the status set the statement asks for, so a report
that stopped filtering on transaction type would still see only anniversaries
here. The RENEW-exclusion test therefore checks the statement itself.
"""
from datetime import date
from unittest import mock

from django.test import SimpleTestCase

from integrations import renewal_report

_OCTOBER_ANNIVERSARY = {
    'policy_number': 'COMG2024103628', 'insured_name': 'Kalahari Trading',
    'broker_name': 'Testbrook Brokers', 'agent_name': 'A Agent',
    'product_name': 'Commercial Insurance', 'freq_code': '3', 'status_code': 1,
    'sum_insured': '', 'annual_premium': 12000,
    'anniv_from': date(2023, 10, 4), 'anniv_to': date(2024, 10, 3),
    'anniv_premium': 12000, 'newbus_from': date(2022, 10, 4),
    'newbus_to': date(2023, 10, 3),
}
_OCTOBER_FIRST_TERM = {
    'policy_number': 'DOMG2026100001', 'insured_name': 'A Household',
    'broker_name': '', 'agent_name': '', 'product_name': 'Domestic Insurance',
    'freq_code': '1', 'status_code': 1, 'sum_insured': '', 'annual_premium': 2400,
    'anniv_from': None, 'anniv_to': None, 'anniv_premium': None,
    'newbus_from': date(2026, 10, 15), 'newbus_to': date(2027, 10, 14),
}
_MARCH_POLICY = dict(_OCTOBER_ANNIVERSARY, policy_number='COMG2024200000',
                     anniv_from=date(2025, 3, 1), anniv_to=date(2026, 2, 28))
_UNKNOWN_FREQUENCY = dict(_OCTOBER_ANNIVERSARY, policy_number='COMG2024300000',
                          freq_code='6')
# Graphite holds no usable premium for about 220 of the 4,112 live policies, and
# at least one carries a real negative. Rounded through `float(x or 0)` both read
# as a confident P0.00 on a money column.
_NO_PREMIUM = dict(_OCTOBER_ANNIVERSARY, policy_number='COMG2024500000',
                   annual_premium=None, anniv_premium=None)
_NEGATIVE_PREMIUM = dict(_OCTOBER_ANNIVERSARY, policy_number='COMG2024600000',
                         annual_premium=-59.99, anniv_premium=-59.99)
_NO_INVOICE = dict(_OCTOBER_ANNIVERSARY, policy_number='COMG2024400000',
                   anniv_from=None, anniv_to=None, anniv_premium=None,
                   newbus_from=None, newbus_to=None)

# Graphite can hold two anniversary invoices on the SAME max date with different
# premiums — a quote and its issued twin, or a reissue. The `av` derived table
# groups on the premium too, so both come back and the same policy would be
# listed twice. This fixture is the duplicate, so the "exactly once" test can
# actually fail.
_DUPLICATE_OF_OCTOBER = dict(_OCTOBER_ANNIVERSARY, anniv_premium=12500)

_ALL = [_OCTOBER_ANNIVERSARY, _DUPLICATE_OF_OCTOBER, _OCTOBER_FIRST_TERM,
        _MARCH_POLICY, _UNKNOWN_FREQUENCY, _NO_INVOICE, _NO_PREMIUM,
        _NEGATIVE_PREMIUM]

_TODAY = date(2026, 9, 15)


def _build(month=10, rows=None, **kwargs):
    with mock.patch.object(renewal_report, 'is_configured', return_value=True), \
         mock.patch.object(renewal_report, 'query',
                           return_value=list(_ALL if rows is None else rows)):
        return renewal_report.build(month, today=_TODAY, **kwargs)


class RenewalMonthTests(SimpleTestCase):

    def test_only_policies_whose_anniversary_falls_in_the_month(self):
        numbers = {r['policy_number'] for r in _build(10)['rows']}
        self.assertIn('COMG2024103628', numbers)
        self.assertIn('DOMG2026100001', numbers)
        self.assertNotIn('COMG2024200000', numbers)   # renews in March

    def test_the_year_is_not_a_filter(self):
        """Finance, 14-Sep: 'regardless of what year it started'.

        The October anniversary is dated 2023 and the report is run in 2026. A
        year filter — which the original spec made mandatory — would lose it.
        """
        rows = _build(10)['rows']
        self.assertTrue(any(r['policy_number'] == 'COMG2024103628' for r in rows))

    def test_a_policy_appears_exactly_once(self):
        result = _build(10)
        numbers = [r['policy_number'] for r in result['rows']]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(numbers.count('COMG2024103628'), 1)
        self.assertTrue(any('more than one anniversary invoice' in e['problem']
                            for e in result['exceptions']),
                        'the duplicate must be flagged, not silently swallowed')

    def test_next_renewal_date_is_the_one_ahead_of_today(self):
        row = next(r for r in _build(10)['rows']
                   if r['policy_number'] == 'COMG2024103628')
        self.assertEqual(row['renewal_effective_date'], '2023-10-04')
        self.assertEqual(row['next_renewal_date'], '2026-10-04')

    def test_a_first_term_policy_is_flagged_as_a_first_renewal(self):
        row = next(r for r in _build(10)['rows']
                   if r['policy_number'] == 'DOMG2026100001')
        self.assertTrue(row['no_issued_anniversary'])
        self.assertEqual(_build(10)['first_renewal_count'], 1)


class InforcePremiumTests(SimpleTestCase):

    def test_the_stored_premium_is_what_they_pay_each_period(self):
        """`annual_premium` is NOT annual — it is the amount on every invoice.

        Proved against policy_ledger on 15-Sep-2026: DOMG2024099537 stores
        290.56 and was invoiced 290.56 in twelve distinct months; COMG2024101598
        stores 21,155.25 and was invoiced that in three. A first pass divided by
        twelve, which would have shown Finance a twelfth of the real in-force
        premium on 2,525 live monthly policies.
        """
        row = next(r for r in _build(10)['rows']
                   if r['policy_number'] == 'DOMG2026100001')
        self.assertEqual(row['payment_frequency'], 'Monthly')
        self.assertEqual(row['inforce_premium'], 2400.0)

    def test_the_renewal_premium_is_the_annualised_figure(self):
        row = next(r for r in _build(10)['rows']
                   if r['policy_number'] == 'DOMG2026100001')
        self.assertEqual(row['renewal_premium'], 28800.0)    # 2400 x 12

    def test_annual_policy_bills_the_same_figure_once(self):
        row = next(r for r in _build(10)['rows']
                   if r['policy_number'] == 'COMG2024103628')
        self.assertEqual(row['inforce_premium'], 12000.0)
        self.assertEqual(row['renewal_premium'], 12000.0)


class HonestGapTests(SimpleTestCase):

    def test_unknown_frequency_costs_the_annual_figure_not_the_inforce_one(self):
        """Code 6 carries 226 live policies and Graphite explains it nowhere.

        Annualising needs the frequency; the in-force premium does not, because
        it is the stored figure untouched. So an unexplained code must NOT cost
        Finance the column they actually asked for.
        """
        result = _build(10)
        row = next(r for r in result['rows']
                   if r['policy_number'] == 'COMG2024300000')
        self.assertIsNone(row['renewal_premium'])
        self.assertEqual(row['inforce_premium'], 12000.0)
        self.assertTrue(any(e['policy_number'] == 'COMG2024300000'
                            for e in result['exceptions']))

    def test_a_flagged_policy_is_never_counted_as_left_off(self):
        """It is on the list. Saying 'N left off' over it states a falsehood."""
        result = _build(10)
        self.assertIn('COMG2024300000',
                      {r['policy_number'] for r in result['rows']})
        self.assertNotIn('COMG2024300000',
                         {e['policy_number'] for e in result['dropped']})
        self.assertIn('COMG2024300000',
                      {e['policy_number'] for e in result['flagged']})

    def test_a_policy_with_no_issued_invoice_is_reported_not_dropped(self):
        result = _build(10)
        self.assertNotIn('COMG2024400000',
                         {r['policy_number'] for r in result['rows']})
        self.assertTrue(any(e['policy_number'] == 'COMG2024400000'
                            for e in result['exceptions']))

    def test_a_missing_premium_is_empty_not_a_confident_zero(self):
        """P0.00 on a money column is a claim. An empty cell is the truth."""
        result = _build(10)
        row = next(r for r in result['rows']
                   if r['policy_number'] == 'COMG2024500000')
        self.assertIsNone(row['inforce_premium'])
        self.assertIsNone(row['renewal_premium'])
        self.assertTrue(any(e['policy_number'] == 'COMG2024500000'
                            and e.get('on_the_list')
                            for e in result['exceptions']))

    def test_a_negative_premium_is_not_annualised_into_a_bigger_negative(self):
        row = next(r for r in _build(10)['rows']
                   if r['policy_number'] == 'COMG2024600000')
        self.assertIsNone(row['inforce_premium'])
        self.assertIsNone(row['renewal_premium'])

    def test_blank_sum_insured_says_why(self):
        """Graphite holds no sum insured for this book on any of 4,134 policies.

        A blank column with no explanation reads as a loading failure, which is
        how a report gets distrusted or, worse, quietly filled with a
        product-level default that is wrong on most rows.
        """
        self.assertTrue(any('Sum insured' in n for n in _build(10)['notes']))

    def test_an_empty_read_is_a_broken_read_not_an_empty_month(self):
        result = _build(10, rows=[])
        self.assertFalse(result['available'])
        self.assertIn('connection', result['reason'])

    def test_a_full_read_refuses_rather_than_showing_a_short_list(self):
        padded = [dict(_OCTOBER_ANNIVERSARY, policy_number=f'COMG{i:010d}')
                  for i in range(renewal_report._READ_LIMIT)]
        result = _build(10, rows=padded)
        self.assertFalse(result['available'])
        self.assertIn('cut short', result['reason'])

    def test_the_next_renewal_date_uses_gaborone_today(self):
        """Botswana is UTC+2. Between midnight and 02:00 CAT a UTC `today` is
        still yesterday, which dates the next renewal a year early and sorts it
        to the top. This class has been fixed twice in this codebase.
        """
        import inspect
        source = inspect.getsource(renewal_report.build)
        self.assertIn('timezone.localdate()', source)
        self.assertNotIn('date.today()', source)

    def test_no_graphite_connection_says_so_instead_of_returning_nothing(self):
        with mock.patch.object(renewal_report, 'is_configured', return_value=False):
            result = renewal_report.build(10)
        self.assertFalse(result['available'])
        self.assertIn('Graphite', result['reason'])
        self.assertEqual(result['rows'], [])


class StatementTests(SimpleTestCase):
    """What the SQL itself must and must not ask for."""

    def test_renew_billing_rows_are_never_read(self):
        """RENEW-ISSUED is the cron's billing cycle, not a renewal.

        28,529 live rows against 2,529 real anniversaries: read them and every
        monthly policy lands on the list twelve times.
        """
        statement = renewal_report._SQL
        self.assertIn("'ANNIVERSARY-RENEW'", statement)
        self.assertIn("'NEWBUSINESS'", statement)
        self.assertNotIn("'RENEW'", statement)

    def test_instant_and_test_policies_are_excluded(self):
        statement = renewal_report._SQL
        self.assertIn('is_test_policy', statement)
        self.assertIn('p.status = 1', statement)

    def test_quotes_are_excluded_by_default(self):
        captured = {}

        def _capture(sql, params=None, **kwargs):
            captured['params'] = params
            return []

        with mock.patch.object(renewal_report, 'is_configured', return_value=True), \
             mock.patch.object(renewal_report, 'query', _capture):
            renewal_report.build(10)
            self.assertEqual(captured['params']['anniv_statuses'], ('ISSUED',))
            renewal_report.build(10, include_quotes=True)
            self.assertEqual(captured['params']['anniv_statuses'],
                             ('ISSUED', 'QUOTE'))

    def test_line_of_business_narrows_to_one_prefix(self):
        captured = {}

        def _capture(sql, params=None, **kwargs):
            captured['params'] = params
            return []

        with mock.patch.object(renewal_report, 'is_configured', return_value=True), \
             mock.patch.object(renewal_report, 'query', _capture):
            renewal_report.build(10, lob='domestic')
            self.assertEqual(captured['params']['prefix_re'], '^DOMG')
            renewal_report.build(10)
            self.assertEqual(captured['params']['prefix_re'], '^(DOMG|COMG)')

    def test_a_bad_month_or_line_of_business_is_refused(self):
        with self.assertRaises(ValueError):
            renewal_report.build(13)
        with self.assertRaises(ValueError):
            renewal_report.build(10, lob='instant')


class NextOccurrenceTests(SimpleTestCase):

    def test_29_february_lands_on_the_28th_in_a_common_year(self):
        got = renewal_report._next_occurrence(date(2024, 2, 29), date(2026, 1, 1))
        self.assertEqual(got, date(2026, 2, 28))

    def test_an_anniversary_already_past_this_year_rolls_to_next_year(self):
        got = renewal_report._next_occurrence(date(2020, 3, 1), date(2026, 9, 15))
        self.assertEqual(got, date(2027, 3, 1))
