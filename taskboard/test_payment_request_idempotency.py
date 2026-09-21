"""taskboard/test_payment_request_idempotency.py — the create endpoint cannot
be applied twice.

CFO 2026-09-14: "payment-related operations can currently be applied twice ...
add an idempotency key so a retry, a double click or a re-sent message cannot
create the same record twice." The real guard is a database UNIQUE
constraint (paymentrequest_one_per_client_request_id, a NON-NULL mirror
column with a partial index — the same pattern as
paymentrequest_one_per_graphite_refund right above it in the model, and
payroll's uniq_orchestration_running_per_period_company); a check-then-write
in Python cannot close a double-click race, so this suite proves the
DATABASE refuses the second row directly, not merely that the view happens
to behave.

Run: manage.py test taskboard.test_payment_request_idempotency
"""
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.utils import DataError
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest
from taskboard.test_helpers import seed_adic

GABS = ZoneInfo('Africa/Gaborone')


def inside_the_load_window():
    return mock.patch('taskboard.payment_views.timezone.localtime',
                      return_value=datetime(2026, 9, 1, 8, 30, tzinfo=GABS))


class DatabaseConstraintTests(TestCase):
    """The constraint itself, independent of the view — this is what proves
    the guard is a real DB-level refusal and not just view-layer plumbing."""

    def test_two_rows_with_the_same_client_request_id_are_refused(self):
        PaymentRequest.objects.create(
            ref='IDEM-1', subject='A', currency='BWP', total='100.00',
            client_request_id='same-key-123')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentRequest.objects.create(
                    ref='IDEM-2', subject='B', currency='BWP', total='200.00',
                    client_request_id='same-key-123')

    def test_two_rows_with_no_client_request_id_do_not_collide(self):
        # The NULL trap this constraint deliberately avoids: if the column
        # were nullable and the constraint keyed on it directly, this would
        # ALSO be true (NULLs never collide) but for the wrong reason and
        # would give no protection once a real key is actually sent. Proven
        # here with the blank-string default every pre-existing/keyless
        # request actually carries.
        PaymentRequest.objects.create(ref='BLANK-1', subject='A', currency='BWP',
                                      total='100.00', client_request_id='')
        PaymentRequest.objects.create(ref='BLANK-2', subject='B', currency='BWP',
                                      total='200.00', client_request_id='')
        self.assertEqual(PaymentRequest.objects.filter(
            client_request_id='').count(), 2)

    def test_two_different_keys_do_not_collide(self):
        PaymentRequest.objects.create(
            ref='IDEM-3', subject='A', currency='BWP', total='100.00',
            client_request_id='key-a')
        PaymentRequest.objects.create(
            ref='IDEM-4', subject='B', currency='BWP', total='200.00',
            client_request_id='key-b')
        self.assertEqual(PaymentRequest.objects.count(), 2)


class CreateEndpointIdempotencyTests(TestCase):
    """The view-level behaviour: a retried submit with the same key is
    handed back the first request, never a second one."""

    def setUp(self):
        self.me = User.objects.create_user('btendani', password='x')
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()

    def post(self, **over):
        body = {
            'subject': 'Consulting invoice',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Consulting', 'amount': '4500.00'}],
            'payee': 'Existing Supplier',
            'account_name': 'Existing Supplier',
            'bank_name': 'FNB',
            'account_number': '62011112222',
        }
        body.update(over)
        with inside_the_load_window():
            return self.client.post(self.url, body, content_type='application/json')

    def test_a_retried_submit_with_the_same_key_creates_only_one_request(self):
        r1 = self.post(client_request_id='click-abc-123')
        self.assertEqual(r1.status_code, 201, r1.content)
        r2 = self.post(client_request_id='click-abc-123')
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertTrue(r2.json().get('replayed'))
        self.assertEqual(r1.json()['id'], r2.json()['id'])
        self.assertEqual(PaymentRequest.objects.count(), 1)

    def test_a_genuinely_new_submit_with_a_different_key_creates_a_second_request(self):
        self.post(client_request_id='click-1')
        self.post(client_request_id='click-2')
        self.assertEqual(PaymentRequest.objects.count(), 2)

    def test_another_users_key_is_never_replayed_back_to_me(self):
        """The key is supplied by the client, so the replay read must be scoped
        to the caller. Unscoped, a second user posting a colliding key is handed
        the FIRST user's payment request — its ref, id, task and assignee — off
        a money endpoint. The uniqueness constraint stays global, so the
        colliding submit is refused rather than duplicated."""
        mine = self.post(client_request_id='shared-key-xyz')
        self.assertEqual(mine.status_code, 201, mine.content)

        someone_else = User.objects.create_user('other_raiser', password='x')
        self.client.force_login(someone_else)
        theirs = self.post(client_request_id='shared-key-xyz')

        # 409, not merely "not 200": the global unique constraint refuses the
        # row, and asserting the exact code keeps a 500 from passing as a pass.
        self.assertEqual(
            theirs.status_code, 409,
            "another user's payment request was replayed back on a colliding "
            "key — that is one user reading another's money record")
        self.assertEqual(PaymentRequest.objects.count(), 1)

    def test_no_key_at_all_still_works_exactly_as_before(self):
        r = self.post()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaymentRequest.objects.get().client_request_id, '')

    def test_a_concurrent_duplicate_that_wins_the_race_is_handed_back_not_re_raised(self):
        """Simulates the double-click race for real: the winner row must NOT
        exist yet when the view's own pre-check runs (that would just hit the
        ordinary replay path, never the race branch this test is named for).
        It is created from inside a mocked _next_ref — called only after the
        pre-check, on every attempt of the retry loop, exactly where a second
        transaction would win the real race — so the create() a few lines
        later hits the database's constraint refusal itself. This is the gap
        Fable 5.1 found on 2026-09-14: the original version created the
        winner up front, so the pre-check alone satisfied it and the
        except-IntegrityError + post-loop replay branches below were never
        actually exercised — deleting them left this test green."""
        def _sneak_in_winner(entity):
            if not PaymentRequest.objects.filter(client_request_id='raced-key').exists():
                PaymentRequest.objects.create(
                    ref='RACE-WINNER', subject='Consulting invoice', currency='BWP',
                    total='4500.00', client_request_id='raced-key',
                    payee='Existing Supplier',
                    # The racing transaction is THIS user's other in-flight
                    # submit — a double click comes from one signed-in session —
                    # so the winner carries the same creator. Leaving it unset
                    # modelled an anonymous third party instead, which the
                    # caller-scoped replay is meant to refuse.
                    created_by=self.me,
                    status=PaymentRequest.Status.PENDING_FINANCE)
            return 'PAY/ADIC/RACE/0001'

        with mock.patch('taskboard.payment_views._next_ref', side_effect=_sneak_in_winner):
            r = self.post(client_request_id='raced-key')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json().get('replayed'))
        self.assertEqual(PaymentRequest.objects.filter(
            client_request_id='raced-key').count(), 1)


class UncategorisedLabelTests(TestCase):
    """CFO 2026-09-14: "make the blank VISIBLE as a problem" — a blank category
    must never disappear from a call site that only prints a CATEGORY line
    when _category_label(...) is truthy; making the blank label itself
    truthy is what fixes that, so both halves are pinned here."""

    def test_blank_category_reads_as_the_visible_uncategorised_label(self):
        from taskboard.payment_views import UNCATEGORISED_LABEL, _category_label
        self.assertEqual(_category_label(''), UNCATEGORISED_LABEL)
        self.assertTrue(_category_label(''),
                        'must be truthy, or `if _category_label(...): ...` call '
                        'sites silently drop the CATEGORY line again')

    def test_an_unrecognised_category_also_reads_as_uncategorised(self):
        from taskboard.payment_views import UNCATEGORISED_LABEL, _category_label
        self.assertEqual(_category_label('not-a-real-category'), UNCATEGORISED_LABEL)

    def test_a_known_category_is_unaffected(self):
        from taskboard.payment_views import _category_label
        self.assertEqual(_category_label(PaymentRequest.Category.CLAIM), 'Claim payments')
