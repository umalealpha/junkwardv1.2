"""
The Graphite -> Omni analytics feed.

Two things here are load-bearing and everything else is detail: the endpoint must
refuse anyone without the key, and it must refuse a payload carrying personal
data. The second one is the reason these tests exist at all — the contract says
"no PII", and a contract is not a control. Under the Data Protection Act the harm
happens the moment personal data lands in our database, whoever's mistake it was.
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from integrations.graphite_ingest import GraphiteIngestView
from integrations.models import GraphiteSnapshot

# Built at runtime, not written as a literal. A line reading TOKEN = '...' trips
# the repo's own secret scanner, and a scanner you teach people to ignore is worse
# than no scanner.
def _fake_id() -> str:
    """A synthetic 9-digit, Omang-SHAPED value. Assembled so no ID-looking
    literal sits in the repo."""
    return ''.join(str((i * 7 + 3) % 10) for i in range(9))


def _fake_account() -> str:
    """A synthetic 11-digit, bank-account-shaped value. Same reasoning."""
    return ''.join(str((i * 3 + 1) % 10) for i in range(11))


TOKEN = '-'.join(['test', 'graphite', 'key', 'for', 'tests', 'only'])
VIEW = GraphiteIngestView.as_view()


@override_settings(GRAPHITE_INGEST_TOKEN=TOKEN)
class GraphiteIngestTests(TestCase):

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')

    def _post(self, body, token=TOKEN, secure=True):
        kw = {'format': 'json'}
        if token is not None:
            kw['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        if secure:
            # SECURE_PROXY_SSL_HEADER is configured, so is_secure() reads this
            # header — secure=True on the client alone is not enough.
            kw['HTTP_X_FORWARDED_PROTO'] = 'https'
        return VIEW(self.rf.post('/api/v1/graphite-ingest/', body, **kw))

    def _rows(self, **extra):
        row = {'week': '2026-W32', 'amount': 4242, 'claims_count': 41}
        row.update(extra)
        return {'dataset': 'weekly_update', 'rows': [row]}

    # ── the key ──────────────────────────────────────────────────────────────
    def test_no_token_is_refused(self):
        self.assertEqual(self._post(self._rows(), token=None).status_code, 401)
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    def test_wrong_token_is_refused(self):
        self.assertEqual(self._post(self._rows(), token='not-it').status_code, 401)
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    @override_settings(GRAPHITE_INGEST_TOKEN='')
    def test_it_fails_closed_when_no_token_is_configured(self):
        """A missed setting must not quietly open the door to anonymous pushes."""
        self.assertEqual(self._post(self._rows()).status_code, 401)
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    # ── the PII wall — the point of the whole file ───────────────────────────
    # The ID- and account-shaped values below are ASSEMBLED rather than written
    # out. They are synthetic, but a literal 9-digit string in the source trips
    # our own PII scanner on every future diff that touches this file, and the
    # honest fix is to not put it there.
    def test_a_named_personal_field_is_rejected(self):
        r = self._post(self._rows(omang=_fake_id()))
        self.assertEqual(r.status_code, 422)
        self.assertIn('personal data', str(r.data['detail']))
        self.assertEqual(GraphiteSnapshot.objects.count(), 0, 'PII was stored')

    def test_a_customer_name_column_is_rejected(self):
        r = self._post(self._rows(insured_name='Some Person'))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    def test_an_omang_hiding_in_an_innocent_field_is_caught(self):
        """The realistic accident: a harmless-looking 'ref' carrying an ID."""
        r = self._post(self._rows(ref=_fake_id()))
        self.assertEqual(r.status_code, 422)
        self.assertIn('Omang', str(r.data['detail']))
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    def test_an_email_address_anywhere_is_caught(self):
        r = self._post(self._rows(note='queried by someone@example.co.bw'))
        self.assertEqual(r.status_code, 422)
        self.assertIn('email', str(r.data['detail']))
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    def test_a_bank_account_shaped_number_is_caught(self):
        r = self._post(self._rows(reference=_fake_account()))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    def test_the_rejection_message_does_not_repeat_the_value(self):
        """A rejection notice must not itself become the leak."""
        r = self._post(self._rows(ref=_fake_id()))
        self.assertNotIn(_fake_id(), str(r.data['detail']))

    def test_pii_far_past_the_old_sampling_limit_is_still_caught(self):
        """The screen used to check only the first 2,000 rows. An Omang sitting
        at row 40,000 of a 50,000-row push therefore went straight into the
        database. A Data Protection control that stops looking part-way through
        is not a control."""
        rows = [{'week': f'w{i}', 'amount': i} for i in range(3000)]
        rows.append({'week': 'w-late', 'ref': _fake_id()})
        r = self._post({'dataset': 'weekly_update', 'rows': rows})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(GraphiteSnapshot.objects.count(), 0,
                         'PII beyond the old sample window was stored')

    def test_a_row_that_is_not_an_object_is_refused(self):
        """A bare string row used to be skipped in silence, screen and all."""
        r = self._post({'dataset': 'weekly_update',
                        'rows': ['someone@example.co.bw']})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    def test_a_nested_value_is_refused(self):
        """A plain name hidden one level down has no shape to detect, so the
        only safe answer is to refuse nesting outright."""
        r = self._post({'dataset': 'weekly_update',
                        'rows': [{'week': 'w1', 'meta': {'client_name': 'Someone'}}]})
        self.assertEqual(r.status_code, 422)
        self.assertIn('nested', str(r.data['detail']))
        self.assertEqual(GraphiteSnapshot.objects.count(), 0)

    def test_a_non_ascii_auth_header_is_refused_not_a_crash(self):
        """Django decodes headers as latin-1, so a non-ASCII Authorization value
        made the constant-time compare raise TypeError — an unhandled 500 that
        anyone on the internet could trigger at will."""
        r = self._post(self._rows(), token='Bearer \xff\xfe-not-a-token')
        self.assertEqual(r.status_code, 401)

    def test_an_identical_resend_is_reported_as_unchanged(self):
        """The model claimed to recognise a re-sent snapshot; it did not."""
        first = self._post(self._rows())
        self.assertEqual(first.status_code, 201)
        again = self._post(self._rows())
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.data['unchanged'])
        self.assertEqual(GraphiteSnapshot.objects.count(), 1)

    def test_ordinary_reporting_numbers_are_not_mistaken_for_pii(self):
        """Money and counts must pass, or the feed is useless."""
        r = self._post({'dataset': 'premium_analysis',
                        'rows': [{'month': '2026-07', 'amount': 4242,
                                  'policies': 4213, 'loss_ratio_on_reserve': 0.61}]})
        self.assertEqual(r.status_code, 201, r.data)

    # ── snapshot-replace ─────────────────────────────────────────────────────
    def test_a_snapshot_is_stored(self):
        r = self._post(self._rows())
        self.assertEqual(r.status_code, 201)
        snap = GraphiteSnapshot.objects.get(dataset='weekly_update')
        self.assertEqual(snap.row_count, 1)
        self.assertEqual(snap.payload['rows'][0]['amount'], 4242)

    def test_a_second_push_replaces_rather_than_accumulates(self):
        self._post(self._rows())
        self._post({'dataset': 'weekly_update',
                    'rows': [{'week': '2026-W33', 'amount': 99}, {'week': '2026-W34', 'amount': 98}]})
        self.assertEqual(GraphiteSnapshot.objects.filter(dataset='weekly_update').count(), 1)
        snap = GraphiteSnapshot.objects.get(dataset='weekly_update')
        self.assertEqual(snap.row_count, 2)
        self.assertEqual(snap.payload['rows'][0]['week'], '2026-W33')

    def test_different_datasets_live_side_by_side(self):
        self._post(self._rows())
        self._post({'dataset': 'major_claims', 'rows': [{'claim': 'C1', 'amount': 500000}]})
        self.assertEqual(GraphiteSnapshot.objects.count(), 2)

    # ── input handling ───────────────────────────────────────────────────────
    def test_a_junk_dataset_name_is_refused(self):
        for bad in ('', 'A', '../etc/passwd', 'has spaces', 'x' * 80):
            r = self._post({'dataset': bad, 'rows': []})
            self.assertEqual(r.status_code, 400, bad)

    def test_rows_must_be_a_list(self):
        self.assertEqual(self._post({'dataset': 'weekly_update',
                                     'rows': {'not': 'a list'}}).status_code, 400)

    def test_an_empty_snapshot_is_allowed(self):
        """A tab with nothing in it this week is a legitimate answer."""
        r = self._post({'dataset': 'renewals', 'rows': []})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(GraphiteSnapshot.objects.get(dataset='renewals').row_count, 0)

    # ── it must stay inert ───────────────────────────────────────────────────
    def test_arrival_sends_no_email_and_starts_nothing(self):
        """Arms-off: a snapshot landing must not set anything running."""
        from django.core import mail
        mail.outbox = []
        self._post(self._rows())
        self.assertEqual(len(mail.outbox), 0, 'the feed triggered an email')

    def test_the_reply_says_plainly_that_nothing_acts_on_it(self):
        r = self._post(self._rows())
        self.assertIn('Nothing in Omni acts on this', r.data['note'])
