"""Tests for the monthly Instant Insurance admin fees (spec B5, 13-Sep-2026).

What is worth pinning here is not "does it build a spreadsheet". It is the small
number of decisions that, if they quietly changed, would pay four retail partners
the wrong amount every month — or, worse, pay one of them nothing and nobody
notice because their name had simply vanished off the report.

Each test below exists because getting it wrong is SILENT.
"""
from __future__ import annotations

import datetime
import pathlib
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from reporting import instant_admin_fees as iaf
from reporting import instant_admin_fees_report as iaf_report
from reporting.models import ReportRecipient


def _row(store='Choppies Enterprises', product='Legal Insurance',
         plan='Standard', policies=1, premium='3127.00'):
    """A row shaped the way the Graphite pivot returns one."""
    return {'store': store, 'product': product, 'plan': plan,
            'policies': policies, 'premium': Decimal(premium)}


def _patch_rows(rows, plan_available=True):
    return mock.patch.object(iaf, 'raw_rows', return_value=(rows, plan_available))


class ChoppiesWorkedExampleTests(TestCase):
    """🔴 THE FIGURE THE WHOLE ITEM TURNS ON.

    The build spec prints the Choppies example as
        3,127.00 -> 1,250.80 -> (153.61) -> 1,097.19 -> (109.72) -> 1,141.08
    and says the tests must reproduce it exactly.

    The first four steps are right. The last number is not: 1,141.08 is
    1,250.80 - 109.72, which is the withholding tax taken off BEFORE the VAT was
    stripped — it contradicts the document's own three steps. The CFO ruled on
    13 September 2026 that the answer is 987.47, because the 10% is charged on
    the fee and not on the VAT: the VAT is not our income.

    987.47 is pinned here. 1,141.08 is pinned as an explicit NOT.
    """

    def test_the_four_steps_the_document_gets_right(self):
        f = iaf.fees(Decimal('3127.00'))
        self.assertEqual(f['commission'], Decimal('1250.80'))   # 40%
        self.assertEqual(f['vat'], Decimal('153.61'))           # stripped out
        self.assertEqual(f['net_of_vat'], Decimal('1097.19'))   # / 1.14
        self.assertEqual(f['owht'], Decimal('109.72'))          # 10% of the fee

    def test_the_payable_is_987_47(self):
        # 1,097.19 - 109.72. The CFO's decision, 13 September 2026.
        self.assertEqual(iaf.fees(Decimal('3127.00'))['payable'],
                         Decimal('987.47'))

    def test_1141_08_is_never_produced(self):
        # The spec's printed answer. It is the VAT-inclusive commission less the
        # tax, and it is wrong. If this ever passes, someone has reinstated the
        # document's arithmetic over the CFO's decision.
        self.assertNotEqual(iaf.fees(Decimal('3127.00'))['payable'],
                            Decimal('1141.08'))

    def test_the_parts_add_back_to_the_commission(self):
        f = iaf.fees(Decimal('3127.00'))
        self.assertEqual(f['net_of_vat'] + f['vat'], f['commission'])
        self.assertEqual(f['payable'] + f['owht'], f['net_of_vat'])


class RoundingTests(TestCase):
    """VAT rounds HALF UP. Rounding is a tax decision, never a language default."""

    def test_money_rounds_half_up_not_to_even(self):
        # Python's round() is banker's rounding: round(0.125, 2) is 0.12.
        self.assertEqual(iaf._money(Decimal('0.125')), Decimal('0.13'))
        self.assertEqual(iaf._money(Decimal('0.135')), Decimal('0.14'))

    def test_the_owht_step_rounds_half_up(self):
        # Premium chosen so the tax step lands exactly on a half:
        #   351.83 * 40%   = 140.732 -> 140.73
        #   140.73 / 1.14  = 123.4473... -> 123.45
        #   123.45 * 10%   = 12.345   -> 12.35 half up   (12.34 to even)
        f = iaf.fees(Decimal('351.83'))
        self.assertEqual(f['commission'], Decimal('140.73'))
        self.assertEqual(f['net_of_vat'], Decimal('123.45'))
        self.assertEqual(f['owht'], Decimal('12.35'))
        self.assertNotEqual(f['owht'], Decimal('12.34'))
        self.assertEqual(f['payable'], Decimal('111.10'))


class VatRateIsAParameterTests(TestCase):
    """The rate is read from settings — never the literal 1.14 in the code."""

    def test_the_divisor_comes_from_settings(self):
        self.assertEqual(iaf.vat_divisor(), Decimal('1.14'))

    @override_settings(RC_VAT_RATE=Decimal('0.15'))
    def test_a_different_rate_moves_the_answer(self):
        f = iaf.fees(Decimal('3127.00'))
        self.assertEqual(iaf.vat_divisor(), Decimal('1.15'))
        self.assertEqual(f['net_of_vat'], Decimal('1087.65'))
        self.assertEqual(f['owht'], Decimal('108.77'))
        self.assertEqual(f['payable'], Decimal('978.88'))
        # If this still came back 987.47 the rate would be hardcoded somewhere.
        self.assertNotEqual(f['payable'], Decimal('987.47'))

    def test_there_is_no_second_vat_constant(self):
        # RC_VAT_RATE is Omni's one VAT rate. A literal rate in this module would
        # be a second place to change when BURS move it, and the two would
        # disagree inside a year.
        src = pathlib.Path(iaf.__file__).read_text(encoding='utf-8')
        code = '\n'.join(l for l in src.splitlines()
                         if not l.strip().startswith('#'))
        self.assertNotIn("Decimal('1.14')", code)
        self.assertNotIn("Decimal('0.14')", code)


class PeriodWindowTests(TestCase):
    """27th to 26th. The month boundary is the trap; December is the worse one."""

    def test_an_ordinary_month(self):
        self.assertEqual(iaf.period_window(2026, 2),
                         (datetime.date(2026, 1, 27), datetime.date(2026, 2, 26)))

    def test_february_to_march_across_a_short_month(self):
        self.assertEqual(iaf.period_window(2026, 3),
                         (datetime.date(2026, 2, 27), datetime.date(2026, 3, 26)))

    def test_january_reaches_back_into_the_previous_year(self):
        # month - 1 on its own gives month 0 and raises; the YEAR has to move
        # with it. Get this wrong and January's report covers nothing at all.
        self.assertEqual(iaf.period_window(2027, 1),
                         (datetime.date(2026, 12, 27), datetime.date(2027, 1, 26)))

    def test_december_stays_inside_its_own_year(self):
        self.assertEqual(iaf.period_window(2026, 12),
                         (datetime.date(2026, 11, 27), datetime.date(2026, 12, 26)))

    def test_the_windows_meet_exactly_with_no_gap_and_no_overlap(self):
        prev_end = iaf.period_window(2026, 12)[1]
        next_start = iaf.period_window(2027, 1)[0]
        self.assertEqual(next_start - prev_end, datetime.timedelta(days=1))

    def test_the_label_names_both_ends(self):
        self.assertEqual(iaf.period_label(2027, 1),
                         '27 Dec 2026 to 26 Jan 2027')


class CurrentPeriodTests(TestCase):
    """Which period a run reports, in Botswana time."""

    def test_on_the_26th_it_is_this_month(self):
        self.assertEqual(iaf.current_period(datetime.date(2026, 9, 26)), (2026, 9))

    def test_after_the_26th_it_is_still_this_month(self):
        self.assertEqual(iaf.current_period(datetime.date(2026, 9, 30)), (2026, 9))

    def test_before_the_26th_it_is_the_month_just_gone(self):
        # A manual run on the 3rd must not open a window running into the future.
        self.assertEqual(iaf.current_period(datetime.date(2026, 9, 3)), (2026, 8))

    def test_early_january_reports_december(self):
        self.assertEqual(iaf.current_period(datetime.date(2027, 1, 3)), (2026, 12))

    def test_botswana_time_not_the_servers_date(self):
        # The server runs UTC; Gaborone is two hours ahead. On the evening of the
        # 25th UTC it is already the 26th there, and that decides the month. The
        # module must therefore take localdate(), never date.today().
        # Checked on the parsed code, not on the text: the docstrings say
        # "never date.today()" in as many words, and a plain string search
        # would pass or fail on the comment rather than on what runs.
        import ast
        tree = ast.parse(pathlib.Path(iaf.__file__).read_text(encoding='utf-8'))
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        self.assertNotIn('today', called)
        self.assertIn('localdate', called)


class EveryMerchantIsNamedTests(TestCase):
    """🔴 "Never omit the merchant silently."

    A merchant missing from the report reads as an oversight and starts a
    conversation. A merchant named, with a nil line, reads as "no business this
    month", which is what it is. The blocks are therefore built from the list of
    four, never from the rows that came back.
    """

    def test_all_four_appear_when_only_one_traded(self):
        with _patch_rows([_row()]):
            data = iaf.collect(2026, 9)
        self.assertEqual([b['merchant'] for b in data['blocks']],
                         ['Choppies', 'Sefalana', 'Trans', 'Yash Cell'])

    def test_all_four_appear_when_nobody_traded(self):
        with _patch_rows([]):
            data = iaf.collect(2026, 9)
        self.assertEqual(len(data['blocks']), 4)
        self.assertTrue(all(b['nil'] for b in data['blocks']))

    def test_the_nil_sentence_is_the_one_the_spec_dictates(self):
        with _patch_rows([_row()]):
            data = iaf.collect(2026, 9)
        sefalana = next(b for b in data['blocks'] if b['merchant'] == 'Sefalana')
        self.assertTrue(sefalana['nil'])
        self.assertEqual(
            sefalana['nil_message'],
            'No instant insurance admin fees recorded for Sefalana in '
            '27 Aug 2026 to 26 Sep 2026.')

    def test_the_nil_sentence_reaches_the_workbook(self):
        # It is no use having the sentence if it never gets printed.
        with _patch_rows([_row()]):
            data = iaf.collect(2026, 9)
        from openpyxl import load_workbook
        wb = load_workbook(iaf_report.build_xlsx(data))
        text = '\n'.join(str(c.value) for row in wb.active.iter_rows()
                         for c in row if c.value is not None)
        for name in ('Sefalana', 'Trans', 'Yash Cell'):
            self.assertIn(f'No instant insurance admin fees recorded for {name}',
                          text)

    def test_the_nil_sentence_reaches_the_email_body(self):
        with _patch_rows([_row()]):
            data = iaf.collect(2026, 9)
        html = iaf_report.build_html(data)
        for name in ('Choppies', 'Sefalana', 'Trans', 'Yash Cell'):
            self.assertIn(name, html)
        self.assertIn('No instant insurance admin fees recorded for Yash Cell',
                      html)


class MerchantMatchingTests(TestCase):
    """Only the four, and each row filed under the right one."""

    def test_the_four_and_only_the_four(self):
        self.assertEqual([m.name for m in iaf.MERCHANTS],
                         ['Choppies', 'Sefalana', 'Trans', 'Yash Cell'])

    def test_a_store_is_matched_case_insensitively(self):
        self.assertEqual(iaf._merchant_for('CHOPPIES ENTERPRISES').name, 'Choppies')
        self.assertEqual(iaf._merchant_for('Sefalana Cash & Carry').name, 'Sefalana')
        self.assertEqual(iaf._merchant_for('Trans Cash & Carry').name, 'Trans')
        self.assertEqual(iaf._merchant_for('Yash Cell Molepolole').name, 'Yash Cell')

    def test_someone_elses_agency_is_not_swept_in(self):
        self.assertIsNone(iaf._merchant_for('Spectrum Insurance Brokers'))
        self.assertIsNone(iaf._merchant_for(''))

    def test_premium_against_an_unmatched_store_is_reported_not_absorbed(self):
        # A pattern edited in one place and not the other would otherwise put
        # someone else's premium quietly into a partner's fee.
        with _patch_rows([_row(store='Some Other Shop', premium='500.00')]):
            data = iaf.collect(2026, 9)
        self.assertIn('Some Other Shop', data['unmatched'])
        self.assertEqual(data['premium'], Decimal('0.00'))
        self.assertIn('Some Other Shop', iaf_report.build_html(data))


class TotalsTests(TestCase):
    """The report has to add up to itself."""

    def test_a_merchant_total_is_computed_on_its_own_premium(self):
        rows = [_row(product='Legal Insurance', premium='1563.50'),
                _row(product='Accidental Death Insurance', premium='1563.50')]
        with _patch_rows(rows):
            data = iaf.collect(2026, 9)
        choppies = data['blocks'][0]
        self.assertEqual(choppies['premium'], Decimal('3127.00'))
        self.assertEqual(choppies['payable'], Decimal('987.47'))

    def test_the_grand_total_covers_every_merchant(self):
        rows = [_row(store='Choppies', premium='3127.00'),
                _row(store='Yash Cell Gabs', premium='3127.00')]
        with _patch_rows(rows):
            data = iaf.collect(2026, 9)
        self.assertEqual(data['premium'], Decimal('6254.00'))
        self.assertEqual(data['payable'], Decimal('1974.94'))
        self.assertEqual(data['policies'], 2)


class SchemaFallbackTests(TestCase):
    """A missing product-plan column degrades; it never sends nothing."""

    def test_the_plan_column_shows_a_dash_and_the_report_says_so(self):
        with _patch_rows([_row(plan='')], plan_available=False):
            data = iaf.collect(2026, 9)
        self.assertFalse(data['plan_available'])
        self.assertEqual(data['blocks'][0]['rows'][0]['plan'], '—')
        self.assertIn('product plan could not be read',
                      iaf_report.build_html(data))

    def test_an_unreadable_replica_is_not_configured_at_all(self):
        with mock.patch.object(iaf.graphite_ro, 'is_configured', return_value=False):
            with self.assertRaises(iaf.GraphiteUnavailable):
                iaf.raw_rows(datetime.date(2026, 8, 27), datetime.date(2026, 9, 26))


class CommandTests(TestCase):
    """It fails the way a person would notice, and posts nothing."""

    def _data(self, rows=None):
        with _patch_rows(rows if rows is not None else [_row()]):
            return iaf.collect(2026, 9)

    def test_it_refuses_to_send_when_graphite_is_unreachable(self):
        # Never a cheerful all-zero email. A broken month must look different
        # from a quiet one.
        with mock.patch.object(iaf, 'collect',
                               side_effect=iaf.GraphiteUnavailable('down')):
            with self.assertRaises(SystemExit) as cm:
                call_command('send_instant_admin_fees', '--month', '2026-09')
        self.assertEqual(cm.exception.code, 2)

    def test_it_refuses_to_send_to_nobody(self):
        with mock.patch.object(iaf, 'collect', return_value=self._data()):
            with self.assertRaises(SystemExit) as cm:
                call_command('send_instant_admin_fees', '--month', '2026-09')
        self.assertEqual(cm.exception.code, 3)

    def test_it_sends_to_the_list_finance_keep(self):
        from django.core import mail
        ReportRecipient.objects.create(
            report_slug='instant-admin-fees-monthly',
            email='rmokgware@alphadirect.co.bw', kind=ReportRecipient.Kind.TO)
        with mock.patch.object(iaf, 'collect', return_value=self._data()):
            call_command('send_instant_admin_fees', '--month', '2026-09')
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn('Instant insurance admin fees', msg.subject)
        self.assertEqual(len(msg.attachments), 1)
        self.assertTrue(msg.attachments[0][0].endswith('2026-09.xlsx'))

    def test_dry_run_sends_nothing(self):
        from django.core import mail
        with mock.patch.object(iaf, 'collect', return_value=self._data()):
            call_command('send_instant_admin_fees', '--month', '2026-09',
                         '--dry-run', '--to', 'someone@alphadirect.co.bw')
        self.assertEqual(len(mail.outbox), 0)

    def test_a_bad_month_is_refused(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('send_instant_admin_fees', '--month', 'September')

    def test_nothing_here_posts_a_journal(self):
        # The fee is submitted to Finance for review and GL posting. That is a
        # human step, and Omni never moves money.
        for mod in (iaf, iaf_report):
            src = pathlib.Path(mod.__file__).read_text(encoding='utf-8')
            code = '\n'.join(l for l in src.splitlines()
                             if not l.strip().startswith('#'))
            self.assertNotIn('JournalEntry', code)
            self.assertNotIn('.save(', code)
