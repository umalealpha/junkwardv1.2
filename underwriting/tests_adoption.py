"""Underwriting tool adoption — the measurements the CFO acts on.

CFO Amendment 3, 2026-09-08: measure who actually USES the Quote builder and
the Document Generator, score each underwriter on it, and chase the low
adopters every Monday.

Every test below is a way the report could quietly mislead him:
  * the person who has NEVER touched either tool dropping off the list — the
    one row he opens the screen to see;
  * a readiness score dividing by zero, or reading 0 for somebody who simply
    had no renewals to do;
  * the seven-day window including or excluding the wrong record;
  * a "0 renewals" line printed in an email when Graphite was simply unreachable;
  * a chase email going to somebody who is already using the tools.

No real staff PII: the names below are invented.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company
from payroll.models import Employee
from underwriting import adoption
from underwriting.management.commands import underwriting_adoption_email as mailer
from underwriting.models import Quote, UnderwritingDocument


def _employee(*, name, username, email, department='Underwriting',
              job_title='Underwriter', company=None, **extra):
    """An active underwriter with an Omni login. Invented person."""
    user = User.objects.create_user(username=username, email=email)
    return Employee.objects.create(
        employee_number=username.upper(),
        full_name=name, department=department, job_title=job_title,
        email=email, company=company, user=user,
        status=Employee.Status.ACTIVE, **extra)


class RosterTests(TestCase):
    """Who counts as an underwriter — and who must never be dropped."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Test Entity', code='TST')
        cls.dineo = _employee(name='Dineo Example', username='dexample',
                              email='dexample@example.test', company=cls.company)
        cls.thabo = _employee(name='Thabo Sample', username='tsample',
                              email='tsample@example.test', company=cls.company,
                              job_title='Underwriting Manager')

    def test_the_underwriting_department_is_matched_loosely(self):
        """'Underwriting & Pricing' is the same team — it must not fall off."""
        _employee(name='Lesego Placeholder', username='lplaceholder',
                  email='lplaceholder@example.test',
                  department='Underwriting & Pricing', company=self.company)
        names = {p['name'] for p in adoption.roster()}
        self.assertIn('Lesego Placeholder', names)

    def test_other_departments_are_not_on_the_roster(self):
        _employee(name='Kagiso Elsewhere', username='kelsewhere',
                  email='kelsewhere@example.test', department='Claims',
                  company=self.company)
        names = {p['name'] for p in adoption.roster()}
        self.assertNotIn('Kagiso Elsewhere', names)

    def test_qa_and_archived_records_are_excluded(self):
        """Automation accounts sat on staff reports once already (2026-08-10)."""
        _employee(name='QA Robot', username='qarobot', email='qa@example.test',
                  company=self.company, is_test_record=True)
        _employee(name='Old Leaver', username='oleaver', email='ol@example.test',
                  company=self.company, is_archived=True)
        names = {p['name'] for p in adoption.roster()}
        self.assertNotIn('QA Robot', names)
        self.assertNotIn('Old Leaver', names)

    def test_the_uw_manager_is_the_cc_list(self):
        self.assertEqual(adoption.manager_emails(), ['tsample@example.test'])


class UsageTests(TestCase):
    """Counted off the rows the tools already write — no event table."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Test Entity', code='TST')
        cls.user_a = _employee(name='Dineo Example', username='dexample',
                               email='dexample@example.test',
                               company=cls.company).user
        # On the roster, has never opened either tool. THIS is the row the CFO
        # opens the screen for.
        cls.never = _employee(name='Neo Neveruser', username='nnever',
                              email='nnever@example.test',
                              company=cls.company).user

    def _quote(self, user, *, when=None, client='A Client'):
        q = Quote.objects.create(client_name=client, premium=Decimal('10000'),
                                 company=self.company, underwriter=user)
        if when is not None:
            Quote.objects.filter(pk=q.pk).update(created_at=when)
        return q

    def _doc(self, user, *, when=None):
        d = UnderwritingDocument.objects.create(
            doctype=UnderwritingDocument.DocType.WCA, fmt='orig',
            company=self.company, issued_by=user,
            status=UnderwritingDocument.Status.ISSUED)
        if when is not None:
            UnderwritingDocument.objects.filter(pk=d.pk).update(created_at=when)
        return d

    def test_a_user_who_has_never_used_either_tool_still_gets_a_row(self):
        start, end = adoption.last_week_window()
        rows = {r['name']: r for r in adoption.usage(start, end)}
        self.assertIn('Neo Neveruser', rows)
        row = rows['Neo Neveruser']
        self.assertEqual(row['quotes'], 0)
        self.assertEqual(row['documents'], 0)
        self.assertIsNone(row['quotes_last_used'])
        self.assertFalse(row['ever_used'])

    def test_quote_builder_and_document_generator_are_counted_separately(self):
        self._quote(self.user_a)
        self._quote(self.user_a)
        self._doc(self.user_a)
        start, end = adoption.last_week_window()
        row = {r['name']: r for r in adoption.usage(start, end)}['Dineo Example']
        self.assertEqual(row['quotes'], 2)
        self.assertEqual(row['documents'], 1)
        self.assertTrue(row['ever_used'])

    def test_last_used_is_all_time_not_just_the_window(self):
        """June usage must show as June — not blank, which reads as 'never'."""
        old = timezone.now() - timedelta(days=90)
        self._quote(self.user_a, when=old)
        start, end = adoption.last_week_window()
        row = {r['name']: r for r in adoption.usage(start, end)}['Dineo Example']
        self.assertEqual(row['quotes'], 0)                 # not in the window
        self.assertIsNotNone(row['quotes_last_used'])       # but it did happen
        self.assertTrue(row['ever_used'])

    # ── the window boundary ─────────────────────────────────────────────────
    def test_a_record_just_inside_seven_days_is_counted(self):
        now = timezone.now()
        self._quote(self.user_a, when=now - timedelta(days=7) + timedelta(seconds=1))
        start, end = adoption.last_week_window(now=now)
        row = {r['name']: r for r in adoption.usage(start, end)}['Dineo Example']
        self.assertEqual(row['quotes'], 1)

    def test_a_record_just_outside_seven_days_is_not_counted(self):
        now = timezone.now()
        self._quote(self.user_a, when=now - timedelta(days=7) - timedelta(seconds=1))
        start, end = adoption.last_week_window(now=now)
        row = {r['name']: r for r in adoption.usage(start, end)}['Dineo Example']
        self.assertEqual(row['quotes'], 0)

    def test_a_record_stamped_exactly_seven_days_ago_is_the_first_in_the_window(self):
        """Half-open [start, end): the boundary belongs to THIS week, once.

        Stated so it cannot drift: if the edge were exclusive at both ends a
        quote could fall between two weekly emails and be chased in neither.
        """
        now = timezone.now()
        self._quote(self.user_a, when=now - timedelta(days=7))
        start, end = adoption.last_week_window(now=now)
        row = {r['name']: r for r in adoption.usage(start, end)}['Dineo Example']
        self.assertEqual(row['quotes'], 1)

    def test_a_record_stamped_now_belongs_to_next_weeks_email(self):
        """The far edge is exclusive, so the same quote is never counted twice."""
        now = timezone.now()
        self._quote(self.user_a, when=now)
        start, end = adoption.last_week_window(now=now)
        row = {r['name']: r for r in adoption.usage(start, end)}['Dineo Example']
        self.assertEqual(row['quotes'], 0)


class ReadinessScoreTests(TestCase):
    """The formula, and the ways a bare number could lie."""

    def test_all_renewals_through_the_tool_is_one_hundred(self):
        self.assertEqual(adoption.readiness_score(10, 10), 100)

    def test_half_through_the_tool_is_fifty(self):
        self.assertEqual(adoption.readiness_score(10, 5), 50)

    def test_no_quotes_at_all_is_zero(self):
        self.assertEqual(adoption.readiness_score(10, 0), 0)

    def test_no_eligible_work_is_not_scored_rather_than_zero(self):
        """0 renewals must NOT read as 0/100 — and must not divide by zero."""
        self.assertIsNone(adoption.readiness_score(0, 0))
        self.assertIsNone(adoption.readiness_score(0, 5))
        self.assertIsNone(adoption.readiness_score(None, None))

    def test_the_score_is_capped_at_one_hundred(self):
        """Spare quotes cannot buy a score above the size of the job."""
        self.assertEqual(adoption.readiness_score(4, 40), 100)

    def test_the_band_puts_the_number_in_plain_words(self):
        self.assertEqual(adoption.readiness_band(None), 'Not scored')
        self.assertEqual(adoption.readiness_band(95), 'Using the tools')
        self.assertEqual(adoption.readiness_band(50), 'Partly manual')
        self.assertEqual(adoption.readiness_band(5), 'Still manual')


def _fake_renewals(by_person, *, available=True, team_total=None, note=''):
    """A stand-in for the Graphite query, injected into weekly_report().

    The live query only reaches the replica from inside the production VPC, so
    the logic around it is proved here with the query faked — the reason
    graphite_renewals() is one isolated function.
    """
    total = sum(by_person.values()) if team_total is None else team_total

    def _fn(start, end):
        return {'available': available, 'by_person': by_person,
                'team_total': total,
                'unattributed': 0, 'note': note}
    return _fn


class WeeklyReportTests(TestCase):
    """Renewals joined to tool usage — the numbers on the screen and the email."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Test Entity', code='TST')
        cls.lazy = _employee(name='Dineo Example', username='dexample',
                             email='dexample@example.test',
                             company=cls.company).user
        cls.keen = _employee(name='Thabo Sample', username='tsample',
                             email='tsample@example.test',
                             company=cls.company).user

    def _quotes(self, user, n):
        for i in range(n):
            Quote.objects.create(client_name=f'Client {i}', premium=Decimal('1000'),
                                 company=self.company, underwriter=user)

    def test_the_gap_is_renewals_minus_quotes(self):
        self._quotes(self.lazy, 2)
        rep = adoption.weekly_report(
            renewals_fn=_fake_renewals({'dineo example': 12, 'thabo sample': 3}))
        rows = {r['name']: r for r in rep['rows']}
        self.assertEqual(rows['Dineo Example']['renewals'], 12)
        self.assertEqual(rows['Dineo Example']['quotes'], 2)
        self.assertEqual(rows['Dineo Example']['gap'], 10)
        self.assertEqual(rows['Dineo Example']['readiness'], 17)

    def test_the_gap_never_goes_negative(self):
        self._quotes(self.keen, 9)
        rep = adoption.weekly_report(renewals_fn=_fake_renewals({'thabo sample': 3}))
        rows = {r['name']: r for r in rep['rows']}
        self.assertEqual(rows['Thabo Sample']['gap'], 0)
        self.assertEqual(rows['Thabo Sample']['readiness'], 100)

    def test_renewals_graphite_cannot_attribute_become_a_team_total(self):
        """Pre-authorised fallback: say so, never guess it onto a person."""
        rep = adoption.weekly_report(
            renewals_fn=_fake_renewals({'dineo example': 4}, team_total=30))
        self.assertEqual(rep['team']['renewals_total'], 30)
        self.assertEqual(rep['team']['renewals_unattributed'], 26)

    def test_an_unreachable_graphite_leaves_the_usage_figures_intact(self):
        self._quotes(self.lazy, 2)
        rep = adoption.weekly_report(renewals_fn=_fake_renewals(
            {}, available=False, note='Renewal figures were not available from Graphite.'))
        rows = {r['name']: r for r in rep['rows']}
        self.assertEqual(rows['Dineo Example']['quotes'], 2)
        self.assertIsNone(rows['Dineo Example']['readiness'])
        self.assertFalse(rep['team']['renewals_available'])
        self.assertEqual(rep['team']['renewals_unattributed'], 0)

    def test_the_team_line_counts_who_has_never_used_anything(self):
        self._quotes(self.keen, 1)
        rep = adoption.weekly_report(renewals_fn=_fake_renewals({}))
        self.assertEqual(rep['team']['headcount'], 2)
        self.assertEqual(rep['team']['never_used'], 1)


class MondayEmailTests(TestCase):
    """The body a low adopter reads, and the body a clean adopter reads."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Test Entity', code='TST')
        cls.lazy = _employee(name='Dineo Example', username='dexample',
                             email='dexample@example.test',
                             company=cls.company).user
        cls.keen = _employee(name='Thabo Sample', username='tsample',
                             email='tsample@example.test',
                             company=cls.company).user

    def _report(self, lazy_quotes=1, keen_quotes=9, renewals=None, **kw):
        for i in range(lazy_quotes):
            Quote.objects.create(client_name=f'L{i}', premium=Decimal('1000'),
                                 company=self.company, underwriter=self.lazy)
        for i in range(keen_quotes):
            Quote.objects.create(client_name=f'K{i}', premium=Decimal('1000'),
                                 company=self.company, underwriter=self.keen)
        renewals = renewals if renewals is not None else {
            'dineo example': 1200, 'thabo sample': 9}
        return adoption.weekly_report(renewals_fn=_fake_renewals(renewals, **kw))

    def _body(self, rep, name):
        row = {r['name']: r for r in rep['rows']}[name]
        return mailer.body_for(row, rep['team'], rep['window'])

    def test_the_low_adopter_is_told_the_renewals_the_tool_did_not_touch(self):
        rep = self._report()
        html = self._body(rep, 'Dineo Example')
        self.assertIn('You renewed <b>1,200</b> policies', html)   # commas, house format
        self.assertIn('You created <b>1</b> quotes in the Quote builder', html)
        self.assertIn('<b>1,199</b> of those renewals did not go through', html)
        self.assertIn('Management is monitoring adoption', html)
        self.assertIn('Please use them', html)

    def test_a_clean_week_is_not_claimed_when_graphite_is_unavailable(self):
        """The first dry-run told a 2-quote underwriter with no known renewals
        that "your renewals went through the tools" — something we had no way
        of knowing. With the renewal count missing, neither body may be used."""
        rep = self._report(renewals={}, available=False,
                           note='Renewal figures were not available from Graphite.')
        html = self._body(rep, 'Thabo Sample')
        self.assertNotIn('went through the tools this week', html)
        self.assertNotIn('did not go through the Quote builder', html)
        self.assertIn('The standard is that every Domestic', html)

    def test_the_clean_adopter_is_not_chased(self):
        rep = self._report()
        html = self._body(rep, 'Thabo Sample')
        self.assertIn('went through the tools this week', html)
        self.assertNotIn('did not go through the Quote builder', html)
        self.assertNotIn('Please use them', html)
        self.assertIn('<b>100</b> out of 100', html)

    def test_a_person_who_has_never_used_either_tool_is_chased_by_name(self):
        rep = self._report(lazy_quotes=0, keen_quotes=0, renewals={})
        html = self._body(rep, 'Dineo Example')
        self.assertIn('not used the Quote builder or the Document Generator at all',
                      html)
        self.assertIn('Last quote built: never', html)

    def test_no_email_ever_names_the_cfo_as_the_enforcer(self):
        """HARD RULE: it is management / the system watching, never a person."""
        rep = self._report()
        for name in ('Dineo Example', 'Thabo Sample'):
            html = self._body(rep, name).lower()
            for banned in ('cfo', 'prathap', 'ganesharajah', 'exco'):
                self.assertNotIn(banned, html, f'{name}: "{banned}" in the body')

    def test_an_unreachable_graphite_prints_no_renewal_figure_at_all(self):
        """A '0 renewals' line the reader knows is wrong discredits the report."""
        rep = self._report(renewals={}, available=False,
                           note='Renewal figures were not available from Graphite.')
        html = self._body(rep, 'Dineo Example')
        self.assertNotIn('You renewed', html)
        self.assertIn('not available from Graphite', html)
        self.assertIn('not scored', html)

    def test_the_subject_carries_the_week_ending_date(self):
        rep = self._report()
        row = rep['rows'][0]
        end = rep['window']['end']
        self.assertEqual(mailer.subject_for(row, rep['window']),
                         f"Underwriting tools — your week to {end:%d %b %Y}")

    def test_the_uw_manager_is_copied_not_chased(self):
        """"Each underwriter individually, with the UW manager copied" — she is
        not addressed to herself, and never cc'd on her own mail."""
        from io import StringIO

        from django.core.management import call_command

        _employee(name='Kefilwe Manager', username='kmanager',
                  email='kmanager@example.test', company=self.company,
                  job_title='Underwriting Manager')
        self._report()
        out = StringIO()
        call_command('underwriting_adoption_email', '--dry-run', stdout=out)
        printed = out.getvalue()
        self.assertNotIn('To:      kmanager@example.test', printed)
        self.assertIn('Cc:      kmanager@example.test', printed)
        self.assertIn('would send 2 email(s)', printed)

    def test_the_house_colours_are_on_the_header(self):
        rep = self._report()
        html = self._body(rep, 'Dineo Example')
        self.assertIn('#0D1B2A', html)      # dark navy header
        self.assertIn('#F4A623', html)      # orange title


class GraphiteQueryShapeTests(TestCase):
    """The one Graphite call — shape only. The live query is NOT run here.

    The read-only bridge reaches the replica from inside the production VPC
    only, so what is provable off-prod is that the statement is scoped the way
    the CFO decided: renewals only, Domestic & Commercial only, and that an
    unreachable replica fails OPEN rather than reporting zero.
    """

    def test_it_asks_only_for_renewal_transactions(self):
        self.assertEqual(adoption.RENEWAL_TRANSACTION_TYPES,
                         ('RENEW', 'ANNIVERSARY-RENEW'))

    def test_it_is_scoped_to_the_domestic_and_commercial_book(self):
        self.assertEqual(adoption.DOM_COM_POLICY_PREFIXES,
                         ('DOMG', 'DOMD', 'COMG', 'COMD'))
        self.assertNotIn('MIS', adoption.DOM_COM_POLICY_PREFIXES)

    def test_the_statement_gets_past_the_shared_pii_guard(self):
        """aware.engine fails CLOSED on any personal-data column — `email`
        included — and rejects SELECT *. If this statement did not pass the
        guard, the whole feature would silently report "Graphite unavailable"
        for ever, because graphite_renewals() swallows the ValueError. So run
        the real guard over the real statement rather than trusting it.
        """
        from unittest.mock import patch

        from aware.engine import _guard_sql

        seen = {}

        def _capture(sql, params):
            seen['guarded'] = _guard_sql(sql)     # raises if the guard objects
            return []

        with patch('aware.engine.run_select_params', side_effect=_capture):
            r = adoption.graphite_renewals(*adoption.last_week_window())
        self.assertTrue(r['available'])
        self.assertIn('policy_actions', seen['guarded'])
        self.assertIn('LIMIT', seen['guarded'].upper())

    def test_an_unreachable_replica_reports_unavailable_not_zero(self):
        from unittest.mock import patch
        with patch('aware.engine.run_select_params',
                   side_effect=OSError('replica unreachable')):
            r = adoption.graphite_renewals(*adoption.last_week_window())
        self.assertFalse(r['available'])
        self.assertEqual(r['by_person'], {})
        self.assertIn('not available', r['note'])

    def test_rows_with_no_staff_name_go_to_the_team_total(self):
        from unittest.mock import patch
        rows = [{'first_name': 'Dineo', 'last_name': 'Example', 'n': 4},
                {'first_name': None, 'last_name': None, 'n': 6}]
        with patch('aware.engine.run_select_params', return_value=rows):
            r = adoption.graphite_renewals(*adoption.last_week_window())
        self.assertTrue(r['available'])
        self.assertEqual(r['by_person'], {'dineo example': 4})
        self.assertEqual(r['unattributed'], 6)
        self.assertEqual(r['team_total'], 10)


class AdoptionEndpointTests(APITestCase):
    """The real request path, /api/v1/underwriting/quotes/adoption/.

    Every model-level test above once passed on this module while the sibling
    Quote viewset was 500-ing on every request because it named a throttle scope
    that did not exist (see QuoteUrlLayerTests in tests_quote.py). A screen is
    not proved by a query — it is proved by a response.
    """

    def setUp(self):
        self.company = Company.objects.create(code='ADO', name='Adoption Co')
        self.staff = User.objects.create_user('uw_adopt', password='x',
                                              is_superuser=True, is_staff=True)
        self.client.force_authenticate(self.staff)
        _employee(name='Dineo Example', username='dexample',
                  email='dexample@example.test', company=self.company)
        _employee(name='Neo Neveruser', username='nnever',
                  email='nnever@example.test', company=self.company)

    def test_the_endpoint_answers_and_lists_everybody(self):
        Quote.objects.create(client_name='A Client', premium=Decimal('5000'),
                             company=self.company,
                             underwriter=User.objects.get(username='dexample'))
        r = self.client.get('/api/v1/underwriting/quotes/adoption/')
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body['window']['days'], 7)
        rows = {row['name']: row for row in body['rows']}
        self.assertEqual(rows['Dineo Example']['quotes'], 1)
        # The row that is the whole point of the screen.
        self.assertFalse(rows['Neo Neveruser']['ever_used'])
        self.assertEqual(body['team']['never_used'], 1)

    def test_the_window_can_be_widened_and_is_bounded(self):
        r = self.client.get('/api/v1/underwriting/quotes/adoption/?days=30')
        self.assertEqual(r.json()['window']['days'], 30)
        # Rubbish and absurd values must not 500 or scan all of history.
        self.assertEqual(
            self.client.get('/api/v1/underwriting/quotes/adoption/?days=abc')
            .json()['window']['days'], 7)
        self.assertEqual(
            self.client.get('/api/v1/underwriting/quotes/adoption/?days=99999')
            .json()['window']['days'], 365)

    def test_it_needs_a_login(self):
        self.client.force_authenticate(None)
        r = self.client.get('/api/v1/underwriting/quotes/adoption/')
        self.assertIn(r.status_code, (401, 403), r.content[:200])

    def test_the_payload_carries_every_field_the_screen_reads(self):
        """The contract with frontend/src/app/(dashboard)/underwriting/adoption.

        That page declares these keys in its AdoptionResponse interface and
        renders them straight out. TypeScript cannot see a Django rename, so a
        field dropped or renamed here would show the CFO a table of blanks and
        dashes with nothing failing anywhere. Keep the two lists in step.
        """
        body = self.client.get('/api/v1/underwriting/quotes/adoption/').json()
        self.assertEqual(set(body), {'window', 'team', 'rows'})
        self.assertEqual(set(body['window']), {'start', 'end', 'days'})
        self.assertEqual(set(body['team']), {
            'renewals_available', 'renewals_note', 'renewals_total',
            'renewals_unattributed', 'quotes_total', 'documents_total',
            'headcount', 'never_used', 'readiness_avg',
            # Added 2026-09-08: the tile now prints the fraction it came from,
            # so the reader can check the number instead of trusting it.
            'readiness_numerator', 'readiness_denominator', 'scored_headcount'})
        self.assertEqual(set(body['rows'][0]), {
            'user_id', 'name', 'job_title', 'is_manager',
            'quotes', 'quotes_last_used', 'documents', 'documents_last_used',
            'ever_used', 'renewals', 'gap', 'readiness', 'readiness_band'})


class TeamReadinessIsWeightedTests(TestCase):
    """The team tile must be weighted by renewals, not a mean of the rows.

    Found by /qctest on 2026-09-08 against live data, confirmed by Fable 5.1.
    Over 30 days the old plain average read **50 / 100** while 17 of 1,181
    renewals had actually gone through the Quote builder. Two underwriters with
    ONE renewal each scored 100 and outweighed a colleague who did 151 by hand,
    and the identical behaviour read 0 over 7 days and 50 over 30 — a tile the
    CFO acts on cannot swing 48 points on the window he happens to pick.

    These tests drive the REAL weekly_report(), with only the Graphite query
    faked. An earlier version of this class recomputed the formula inside the
    test and therefore passed against the broken code too — which proves
    nothing at all.
    """

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Weighted Co', code='WGT')
        # The four real production rows from the /qctest run, renamed.
        cls.big   = _employee(name='Big Book', username='bigbook',
                              email='bigbook@example.test',
                              company=cls.company).user      # 151 renewals, 0 quotes
        cls.tiny1 = _employee(name='Tiny One', username='tiny1',
                              email='tiny1@example.test',
                              company=cls.company).user      # 1 renewal, 16 quotes
        cls.tiny2 = _employee(name='Tiny Two', username='tiny2',
                              email='tiny2@example.test',
                              company=cls.company).user      # 1 renewal, 1 quote
        cls.mid   = _employee(name='Mid Book', username='midbook',
                              email='midbook@example.test',
                              company=cls.company).user      # 66 renewals, 0 quotes

    def _quotes(self, user, n):
        for i in range(n):
            Quote.objects.create(client_name=f'C{i}', premium=Decimal('1000'),
                                 company=self.company, underwriter=user)

    def _team(self, renewals):
        return adoption.weekly_report(
            renewals_fn=_fake_renewals(renewals))['team']

    def test_the_live_rows_that_read_fifty_now_read_one(self):
        self._quotes(self.tiny1, 16)
        self._quotes(self.tiny2, 1)
        team = self._team({'big book': 151, 'tiny one': 1,
                           'tiny two': 1, 'mid book': 66})
        # The plain mean of the row scores was (0+100+100+0)/4 = 50.
        self.assertEqual(team['readiness_avg'], 1)
        self.assertEqual(team['readiness_numerator'], 2)
        self.assertEqual(team['readiness_denominator'], 219)
        self.assertEqual(team['scored_headcount'], 4)

    def test_one_renewal_cannot_outweigh_a_hundred_and_fifty(self):
        self._quotes(self.tiny2, 1)
        alone = self._team({'big book': 151})
        plus  = self._team({'big book': 151, 'tiny two': 1})
        self.assertEqual(alone['readiness_avg'], 0)
        self.assertLessEqual(plus['readiness_avg'], 1,
                             'one renewal moved the team tile too far')

    def test_spare_quotes_cannot_lift_the_team(self):
        # 999 quotes against 1 renewal still counts as 1 — the per-person cap
        # survives inside the numerator, so quote-spam buys nothing.
        self._quotes(self.tiny1, 999)
        team = self._team({'big book': 100, 'tiny one': 1})
        self.assertEqual(team['readiness_avg'], 1)

    def test_a_team_that_really_uses_the_tool_still_reads_high(self):
        self._quotes(self.big, 151)
        self._quotes(self.mid, 66)
        team = self._team({'big book': 151, 'mid book': 66})
        self.assertEqual(team['readiness_avg'], 100)

    def test_nobody_with_a_renewal_is_not_scored_rather_than_zero(self):
        self._quotes(self.tiny1, 5)
        team = self._team({})
        self.assertIsNone(team['readiness_avg'])

    def test_the_tile_reconciles_to_the_rows_beneath_it(self):
        self._quotes(self.tiny1, 16)
        self._quotes(self.tiny2, 1)
        rep = adoption.weekly_report(renewals_fn=_fake_renewals(
            {'big book': 151, 'tiny one': 1, 'tiny two': 1, 'mid book': 66}))
        scored = [r for r in rep['rows'] if r['readiness'] is not None]
        num = sum(min(r['quotes'], r['renewals']) for r in scored)
        den = sum(r['renewals'] for r in scored)
        self.assertEqual(rep['team']['readiness_numerator'], num)
        self.assertEqual(rep['team']['readiness_denominator'], den)
        self.assertEqual(rep['team']['readiness_avg'], round(100 * num / den))
