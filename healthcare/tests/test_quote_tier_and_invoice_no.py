"""The three follow-ups the CFO released on 2026-07-30 (bug 03a2b875).

  (a) one plan for the whole group on upload — a census with no Tier column used
      to exclude every member and price to zero;
  (b) a missing plan no longer silently prices on AD_ESSENTIAL — the wrong plan
      is worse than a zero, so the tier is now required at the API boundary;
  (c) invoice numbers can no longer collide or be reused — allocation is
      serialised and derived from MAX, with a database uniqueness guarantee.

Synthetic members only — never real member data.
"""

import datetime
import io
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIClient, APITestCase

from healthcare.models import HealthQuote, HealthQuoteMember
from healthcare.quote_views import _next_invoice_no


def _sheet(rows, headers=('Full Name', 'Status', 'Gender', 'Date of Birth')):
    """A member schedule with NO Tier column — the shape that broke."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(headers))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = 'members.xlsx'
    return buf


CENSUS = [
    ('Test Member One', 'Main', 'M', datetime.date(1990, 5, 12)),
    ('Test Member Two', 'Spouse', 'F', datetime.date(1992, 3, 4)),
    ('Test Member Three', 'Child', 'M', datetime.date(2015, 1, 20)),
]


class GroupPlanUploadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('gtier', 'gtier@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = '/api/v1/health/quotes/parse-members/'

    def test_no_tier_column_and_no_group_plan_still_rates_nothing(self):
        """The old behaviour, kept: without a plan we cannot invent one."""
        r = self.client.post(self.url, {'file': _sheet(CENSUS)}, format='multipart')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['count'], 0)
        self.assertEqual(len(r.json()['errors']), 3)
        self.assertIn('whole group', r.json()['errors'][0]['error'])

    def test_group_plan_rates_the_whole_census(self):
        r = self.client.post(self.url, {'file': _sheet(CENSUS), 'group_tier': 'AD_CORE'},
                             format='multipart')
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body['count'], 3, body)
        self.assertEqual(body['errors'], [])
        self.assertEqual(body['group_tier_applied'], 3)
        self.assertTrue(all(m['tier'] == 'AD_CORE' for m in body['members']))
        self.assertTrue(any(Decimal(m['premium_excl']) > 0 for m in body['members']),
                        'the whole point: the census now carries real premium')

    def test_group_plan_accepts_a_friendly_name(self):
        r = self.client.post(self.url, {'file': _sheet(CENSUS), 'group_tier': 'AD Premier'},
                             format='multipart')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['group_tier'], 'AD_PREMIER')

    def test_a_rows_own_plan_wins_over_the_group_plan(self):
        rows = [c + ('AD Status',) for c in CENSUS[:1]] + [c + ('',) for c in CENSUS[1:]]
        r = self.client.post(
            self.url,
            {'file': _sheet(rows, headers=('Full Name', 'Status', 'Gender', 'Date of Birth', 'Tier')),
             'group_tier': 'AD_LITE'},
            format='multipart')
        body = r.json()
        self.assertEqual(body['count'], 3, body)
        tiers = {m['full_name']: m['tier'] for m in body['members']}
        self.assertEqual(tiers['Test Member One'], 'AD_STATUS', 'mixed-plan schedules must survive')
        self.assertEqual(tiers['Test Member Two'], 'AD_LITE')
        self.assertEqual(body['group_tier_applied'], 2)

    def test_a_nonsense_group_plan_is_rejected(self):
        r = self.client.post(self.url, {'file': _sheet(CENSUS), 'group_tier': 'Platinum Deluxe'},
                             format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertIn('not a plan', r.json()['detail'])


class TierRequiredOnSaveTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('treq', 'treq@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _payload(self, tier):
        return {'client_name': 'Test Employer',
                'members': [{'full_name': 'Test Member', 'member_type': 'main', 'gender': 'M',
                             'date_of_birth': '1990-05-12', 'tier': tier}]}

    def test_missing_tier_is_refused_not_priced_as_essential(self):
        r = self.client.post('/api/v1/health/quotes/', self._payload(''), format='json')
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertIn('no plan set', r.json()['detail'])
        self.assertEqual(HealthQuote.objects.count(), 0)

    def test_unknown_tier_is_refused(self):
        r = self.client.post('/api/v1/health/quotes/', self._payload('Gold'), format='json')
        self.assertEqual(r.status_code, 400)

    def test_a_real_tier_still_saves(self):
        r = self.client.post('/api/v1/health/quotes/', self._payload('AD_CORE'), format='json')
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertEqual(HealthQuote.objects.get().members.get().tier, 'AD_CORE')

    def test_patch_also_refuses_a_missing_tier(self):
        r = self.client.post('/api/v1/health/quotes/', self._payload('AD_CORE'), format='json')
        qid = r.json()['id']
        r2 = self.client.patch(f'/api/v1/health/quotes/{qid}/',
                               {'members': [{'full_name': 'Test Member', 'member_type': 'main',
                                             'gender': 'M', 'date_of_birth': '1990-05-12',
                                             'tier': ''}]}, format='json')
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(HealthQuote.objects.get(id=qid).members.get().tier, 'AD_CORE',
                         'the good rows must be left alone when the edit is refused')

    def test_blank_named_rows_are_ignored_not_rejected(self):
        body = {'client_name': 'Test Employer',
                'members': [{'full_name': 'Test Member', 'member_type': 'main', 'gender': 'M',
                             'date_of_birth': '1990-05-12', 'tier': 'AD_CORE'},
                            {'full_name': '   ', 'member_type': 'main', 'gender': 'M',
                             'date_of_birth': '', 'tier': ''}]}
        r = self.client.post('/api/v1/health/quotes/', body, format='json')
        self.assertEqual(r.status_code, 201, r.content[:300])


class InvoiceNumberTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('inv', 'inv@alphadirect.co.bw', 'x')

    def _quote(self, ref, client_name='Mivani Security', invoice_no='', year=None):
        q = HealthQuote.objects.create(client_name=client_name, ref=ref, created_by=self.user)
        if invoice_no:
            q.invoice_no = invoice_no
            q.invoice_date = datetime.date(year or datetime.date.today().year, 6, 1)
            q.save(update_fields=['invoice_no', 'invoice_date'])
        return q

    def test_first_number_of_the_year(self):
        with transaction.atomic():
            no, _ = _next_invoice_no('Mivani Security')
        self.assertEqual(no, f'MIV-{datetime.date.today().year}001')

    def test_number_comes_from_the_highest_used_not_the_count(self):
        """A deleted invoice must not hand its number to the next one."""
        year = datetime.date.today().year
        self._quote('R1', invoice_no=f'AAA-{year}001')
        self._quote('R2', invoice_no=f'BBB-{year}007')
        with transaction.atomic():
            no, _ = _next_invoice_no('Mivani Security')
        self.assertEqual(no, f'MIV-{year}008', 'must continue past the highest, not re-use 003')

    def test_short_and_punctuated_client_names_still_get_a_prefix(self):
        with transaction.atomic():
            self.assertTrue(_next_invoice_no('3M')[0].startswith('M-'))
            self.assertTrue(_next_invoice_no('123 (Pty) Ltd')[0].startswith('PTY-'))

    def test_last_years_numbers_do_not_raise_this_years_sequence(self):
        year = datetime.date.today().year
        self._quote('R3', invoice_no=f'OLD-{year - 1}099', year=year - 1)
        with transaction.atomic():
            no, _ = _next_invoice_no('Mivani Security')
        self.assertEqual(no, f'MIV-{year}001')

    def test_the_database_refuses_a_duplicate_invoice_number(self):
        year = datetime.date.today().year
        self._quote('R4', invoice_no=f'DUP-{year}001')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._quote('R5', invoice_no=f'DUP-{year}001')

    def test_many_un_invoiced_quotes_do_not_clash(self):
        """Blank invoice_no is the normal state — the constraint must allow many."""
        for i in range(4):
            self._quote(f'R6{i}')
        self.assertEqual(HealthQuote.objects.filter(invoice_no='').count(), 4)


class InvoiceEndpointNumberTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('inve', 'inve@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_invoicing_an_approved_quote_allocates_one_number_and_keeps_it(self):
        q = HealthQuote.objects.create(client_name='Mivani Security', ref='IE-1',
                                       created_by=self.user, status='approved')
        excl = Decimal('281')
        HealthQuoteMember.objects.create(
            quote=q, full_name='Test Member', member_type='main', gender='M',
            date_of_birth=datetime.date(1990, 1, 1), age=36, age_band='35-39',
            tier='AD_CORE', premium_excl=excl, vat=Decimal('39.34'), premium_incl=Decimal('320.34'))
        q.gross_excl = q.subtotal_excl = excl
        q.vat = Decimal('39.34')
        q.total_incl = Decimal('320.34')
        q.save()

        r = self.client.post(f'/api/v1/health/quotes/{q.id}/invoice/')
        self.assertEqual(r.status_code, 200, r.content[:300])
        q.refresh_from_db()
        first = q.invoice_no
        self.assertTrue(first, 'an invoice number must be issued')

        # Re-invoicing must not burn a second number.
        r2 = self.client.post(f'/api/v1/health/quotes/{q.id}/invoice/')
        self.assertEqual(r2.status_code, 200)
        q.refresh_from_db()
        self.assertEqual(q.invoice_no, first)


class RateMemberBackstopTests(APITestCase):
    """DeepSeek review 2026-07-30: the silent AD_ESSENTIAL fallback had to go, not
    just be guarded upstream, or it would creep back."""

    def test_rate_member_refuses_a_missing_plan(self):
        from healthcare.quote_views import _rate_member
        with self.assertRaises(ValueError):
            _rate_member({'full_name': 'Test Member', 'gender': 'M',
                          'date_of_birth': '1990-01-01'}, datetime.date.today())

    def test_rate_member_refuses_an_unknown_plan(self):
        from healthcare.quote_views import _rate_member
        with self.assertRaises(ValueError):
            _rate_member({'full_name': 'Test Member', 'gender': 'M', 'tier': 'Gold',
                          'date_of_birth': '1990-01-01'}, datetime.date.today())

    def test_rate_member_still_prices_a_real_plan(self):
        from healthcare.quote_views import _rate_member
        r = _rate_member({'full_name': 'Test Member', 'gender': 'M', 'tier': 'AD_CORE',
                          'date_of_birth': '1990-01-01'}, datetime.date.today())
        self.assertEqual(r['tier'], 'AD_CORE')
        self.assertGreater(Decimal(r['premium_excl']), 0)


class InvoiceNumberConcurrencyTests(TransactionTestCase):
    """The advisory lock has to hold under a REAL race, not just in theory.

    DeepSeek review 2026-07-30 asked for this. TransactionTestCase (not
    APITestCase) because each thread needs its own committed transaction.
    """
    reset_sequences = True

    def test_eight_threads_allocating_at_once_get_eight_distinct_numbers(self):
        import threading
        from django.db import connections

        user = User.objects.create_superuser('conc', 'conc@alphadirect.co.bw', 'x')
        quotes = [HealthQuote.objects.create(client_name='Mivani Security',
                                            ref=f'CC-{i}', created_by=user)
                  for i in range(8)]
        results, errors = [], []
        barrier = threading.Barrier(len(quotes))

        def allocate(q):
            try:
                barrier.wait(timeout=10)          # all threads hit the lock together
                with transaction.atomic():
                    no, date = _next_invoice_no(q.client_name)
                    q.invoice_no, q.invoice_date = no, date
                    q.save(update_fields=['invoice_no', 'invoice_date'])
                results.append(no)
            except Exception as exc:              # noqa: BLE001
                errors.append(repr(exc))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=allocate, args=(q,)) for q in quotes]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f'no allocation may fail: {errors}')
        self.assertEqual(len(results), 8)
        self.assertEqual(len(set(results)), 8,
                         f'every invoice number must be unique, got {sorted(results)}')
        year = datetime.date.today().year
        self.assertEqual(sorted(results),
                         [f'MIV-{year}{i:03d}' for i in range(1, 9)],
                         'and they must be a clean unbroken run')


class GarbledTierIsNotSilentlyRePlannedTests(APITestCase):
    """Fable review 2026-07-30 (fix 2): the group plan must fill only genuinely
    BLANK plan cells. A row naming a plan we cannot read ('AD Premeir') has to
    stay an error — quietly re-planning it prices a member on a plan nobody chose
    for them, and a wrong premium is worse than a zero."""

    def setUp(self):
        self.user = User.objects.create_superuser('garb', 'garb@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = '/api/v1/health/quotes/parse-members/'

    def test_a_misspelt_plan_is_flagged_not_moved_to_the_group_plan(self):
        rows = [CENSUS[0] + ('AD Premeir',), CENSUS[1] + ('',)]
        r = self.client.post(
            self.url,
            {'file': _sheet(rows, headers=('Full Name', 'Status', 'Gender', 'Date of Birth', 'Tier')),
             'group_tier': 'AD_LITE'},
            format='multipart')
        body = r.json()
        rated = {m['full_name']: m['tier'] for m in body['members']}
        self.assertNotIn('Test Member One', rated,
                         'the misspelt row must NOT be silently priced on the group plan')
        self.assertEqual(rated.get('Test Member Two'), 'AD_LITE', 'the blank row still gets it')
        self.assertEqual(body['group_tier_applied'], 1)
        self.assertEqual(len(body['errors']), 1)
        self.assertIn('not recognised', body['errors'][0]['error'])

    def test_whitespace_only_plan_counts_as_blank(self):
        rows = [CENSUS[0] + ('   ',)]
        r = self.client.post(
            self.url,
            {'file': _sheet(rows, headers=('Full Name', 'Status', 'Gender', 'Date of Birth', 'Tier')),
             'group_tier': 'AD_LITE'},
            format='multipart')
        body = r.json()
        self.assertEqual(body['count'], 1, body)
        self.assertEqual(body['members'][0]['tier'], 'AD_LITE')


class SameQuoteDoubleInvoiceTests(TransactionTestCase):
    """Fable review 2026-07-30 (fix 1): a double-click on Invoice used to allocate
    TWO numbers for ONE quote — the second overwrote the first, leaving a gap in
    the register and a number already printed on a PDF. Uniqueness cannot catch
    it because the overwriting number is itself unique."""
    reset_sequences = True

    def test_two_simultaneous_invoice_posts_allocate_exactly_one_number(self):
        import threading
        from django.db import connections

        user = User.objects.create_superuser('dbl', 'dbl@alphadirect.co.bw', 'x')
        q = HealthQuote.objects.create(client_name='Mivani Security', ref='DBL-1',
                                       created_by=user, status='approved')
        excl = Decimal('281')
        HealthQuoteMember.objects.create(
            quote=q, full_name='Test Member', member_type='main', gender='M',
            date_of_birth=datetime.date(1990, 1, 1), age=36, age_band='35-39',
            tier='AD_CORE', premium_excl=excl, vat=Decimal('39.34'),
            premium_incl=Decimal('320.34'))
        q.gross_excl = q.subtotal_excl = excl
        q.vat = Decimal('39.34')
        q.total_incl = Decimal('320.34')
        q.save()

        codes, barrier = [], threading.Barrier(2)

        def hit():
            try:
                c = APIClient()
                c.force_authenticate(user)
                barrier.wait(timeout=10)
                r = c.post(f'/api/v1/health/quotes/{q.id}/invoice/')
                codes.append(r.status_code)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=hit) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(codes), [200, 200], f'both clicks should succeed, got {codes}')
        q.refresh_from_db()
        year = datetime.date.today().year
        self.assertEqual(q.invoice_no, f'MIV-{year}001',
                         'one quote, one number — no second number burned')
        self.assertEqual(HealthQuote.objects.exclude(invoice_no='').count(), 1)
