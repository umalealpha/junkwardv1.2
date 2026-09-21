"""Money Lens + one-tap CEO decisions (CFO 2026-09-10)."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase

from core import ceo_money_lens as ml
from core.ceo_monitor_views import (_DECIDE_SALT, _detail_source,
                                    is_decidable_task, make_decision_token)
from core.models import OmniTask

User = get_user_model()


class MoneyLensTests(TestCase):
    def test_a_failed_query_reads_unavailable_and_never_zero(self):
        """The whole point of the tile state: a broken query must not tell the
        CEO that collections were zero."""
        with mock.patch.object(ml, "_one", return_value=None), \
                mock.patch.object(ml, "_claims_paid",
                                  return_value=ml._tile("Claims paid", None,
                                                        "no fresh data", None)):
            lens = ml.money_lens(datetime.date(2026, 9, 10))
        for tile in lens["tiles"]:
            self.assertFalse(tile["ok"])
            self.assertIsNone(tile["value"])
            self.assertIsNone(tile["delta"])

    def test_it_measures_trailing_mtd_against_prior_month(self):
        seen = []

        def _spy(sql):
            seen.append(sql)
            return {"n": 1, "t": 100.0, "c": 1, "p": 100.0}

        with mock.patch.object(ml, "_one", side_effect=_spy), \
                mock.patch.object(ml, "_claims_paid",
                                  return_value=ml._tile("Claims paid", "P0",
                                                        "0 payments", None)):
            lens = ml.money_lens(datetime.date(2026, 9, 10))
        joined = " ".join(seen)
        self.assertIn("2026-09-01", joined)   # MTD start
        self.assertIn("2026-09-09", joined)   # yesterday
        self.assertIn("2026-08-01", joined)   # prior month start
        self.assertIn("2026-08-09", joined)   # prior month same length
        self.assertIn("01 Sep", lens["period"])

    def test_a_zero_baseline_gives_no_percentage(self):
        self.assertIsNone(ml._delta(500.0, 0))
        self.assertIsNone(ml._delta(500.0, None))
        self.assertAlmostEqual(ml._delta(150.0, 100.0), 50.0)

    def test_premium_is_cast_before_summing(self):
        """v_policy_payments.amount is a varchar on the replica — a plain SUM
        would silently return garbage."""
        seen = []
        with mock.patch.object(ml, "_one", side_effect=lambda s: seen.append(s)):
            ml._collected("2026-09-01", "2026-09-09", "2026-08-01", "2026-08-09")
        self.assertIn("CAST(amount AS DECIMAL", seen[0])


class DecidableGateTests(TestCase):
    def setUp(self):
        self.ceo = User.objects.create(username="aiyer",
                                       email="aiyer@alphadirect.co.bw")
        self.staff = User.objects.create(username="someone",
                                         email="someone@alphadirect.co.bw")

    def _task(self, **kw):
        kw.setdefault("title", "Approve the new broker agreement")
        kw.setdefault("status", OmniTask.Status.PENDING)
        return OmniTask.objects.create(assigner=self.staff, assignee=self.ceo, **kw)

    def test_an_ordinary_task_is_decidable(self):
        self.assertTrue(is_decidable_task(self._task()))

    def test_a_payment_task_is_never_decidable_from_the_email(self):
        """Completing a payment_request task advances the linked request to
        PAID — that click keeps its own duplicate checks and typed note, so it
        must stay in Omni."""
        self.assertFalse(is_decidable_task(self._task(source="payment_request")))

    def test_a_closed_task_is_not_decidable(self):
        self.assertFalse(is_decidable_task(self._task(status=OmniTask.Status.DONE)))
        self.assertFalse(is_decidable_task(
            self._task(status=OmniTask.Status.CANCELLED)))

    def test_the_gate_fails_closed_on_an_unreadable_task(self):
        self.assertFalse(is_decidable_task(object()))


class DecideEndpointTests(TestCase):
    def setUp(self):
        self.ceo = User.objects.create(username="aiyer",
                                       email="aiyer@alphadirect.co.bw")
        self.staff = User.objects.create(username="someone",
                                         email="someone@alphadirect.co.bw")
        self.task = OmniTask.objects.create(
            assigner=self.staff, assignee=self.ceo,
            title="Approve the new broker agreement")

    def _url(self, action, task=None):
        tok = make_decision_token(task_id=(task or self.task).pk, action=action)
        return "/api/ceo-monitor/decide/?t=" + tok, tok

    def test_get_only_confirms_and_changes_nothing(self):
        """Microsoft 365 pre-fetches links in delivered mail with GET — a
        side-effect on GET would fire every button in the brief."""
        url, _ = self._url("approve")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Confirm", r.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)

    def test_post_declines_and_closes(self):
        _, tok = self._url("decline")
        r = self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.assertEqual(r.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.CANCELLED)

    def test_need_detail_keeps_the_task_open_and_asks_the_raiser(self):
        _, tok = self._url("detail")
        self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)
        self.assertTrue(OmniTask.objects.filter(
            assignee=self.staff, source=_detail_source(self.task)).exists())

    def test_need_detail_twice_does_not_raise_two_tasks(self):
        _, tok = self._url("detail")
        self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.assertEqual(OmniTask.objects.filter(
            assignee=self.staff, source=_detail_source(self.task)).count(), 1)

    def test_a_payment_task_is_refused_by_the_endpoint_too(self):
        pay = OmniTask.objects.create(assigner=self.staff, assignee=self.ceo,
                                      title="Pay ABC Motors", source="payment_request")
        _, tok = self._url("approve", task=pay)
        r = self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"in Omni", r.content)
        pay.refresh_from_db()
        self.assertEqual(pay.status, OmniTask.Status.PENDING)

    def test_a_forged_token_is_rejected(self):
        r = self.client.post("/api/ceo-monitor/decide/", {"t": "not-a-token"})
        self.assertEqual(r.status_code, 400)

    def test_a_token_for_another_purpose_is_rejected(self):
        """A token minted with the escalation salt must not work here."""
        tok = signing.dumps({"task": str(self.task.pk), "action": "approve"},
                            salt="ceo-monitor-escalate")
        r = self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.assertEqual(r.status_code, 400)

    def test_an_unknown_action_is_rejected(self):
        tok = signing.dumps({"task": str(self.task.pk), "action": "pay"},
                            salt=_DECIDE_SALT)
        r = self.client.post("/api/ceo-monitor/decide/", {"t": tok})
        self.assertEqual(r.status_code, 400)


class ClaimsPaidTileTests(TestCase):
    """CFO 2026-09-10: actual claims payments only — never reserves."""

    def test_it_reads_paid_claim_requests_not_reserves(self):
        from taskboard.models import PaymentRequest
        tile = ml._claims_paid("2026-09-09", "2026-09-02", "2026-09-01", "2026-08-26")
        self.assertEqual(tile["label"], "Claims paid")
        self.assertEqual(tile["note"], "money out, not reserves")
        # nothing in the test DB -> a real, verified zero (not "unknown")
        self.assertTrue(tile["ok"])
        self.assertEqual(tile["value"], "P0")
        del PaymentRequest

    def test_a_broken_query_is_unknown_not_zero(self):
        with mock.patch("taskboard.models.PaymentRequest.objects") as objs:
            objs.filter.side_effect = RuntimeError("replica down")
            tile = ml._claims_paid("2026-09-09", "2026-09-02", "2026-09-01", "2026-08-26")
        self.assertFalse(tile["ok"])
        self.assertIsNone(tile["value"])
        self.assertEqual(tile["sub"], "no fresh data")


class PaymentRequestGateRegressionTests(TestCase):
    """The reverse accessor `task.payment_request` is a MANAGER (PaymentRequest
    points at OmniTask with a ForeignKey), so `if task.payment_request:` is
    always True. That marked every task financial and silently removed every
    button from the brief. This is the test that fails without the .exists()
    fix."""

    def setUp(self):
        self.ceo = User.objects.create(username="aiyer",
                                       email="aiyer@alphadirect.co.bw")
        self.staff = User.objects.create(username="someone2",
                                         email="someone2@alphadirect.co.bw")

    def test_a_task_with_no_payment_request_is_still_decidable(self):
        task = OmniTask.objects.create(assigner=self.staff, assignee=self.ceo,
                                       title="Sign the broker agreement")
        self.assertTrue(bool(task.payment_request),
                        "guard: the reverse manager is truthy — that is the trap")
        self.assertFalse(task.payment_request.exists())
        self.assertTrue(is_decidable_task(task))

    def test_a_task_with_a_linked_payment_request_is_not_decidable(self):
        from taskboard.models import PaymentRequest
        task = OmniTask.objects.create(assigner=self.staff, assignee=self.ceo,
                                       title="Pay the panel beater")
        PaymentRequest.objects.create(task=task, created_by=self.staff,
                                      category=PaymentRequest.Category.CLAIM,
                                      total=1000)
        self.assertTrue(task.payment_request.exists())
        self.assertFalse(is_decidable_task(task))

class LargeClaimFloorTests(TestCase):
    """The CEO is not told about a P15 key loss (CFO, 16-Sep-2026).

    Before the floor the only filter was `reserve_amount > 0`, so on a quiet
    week the brief led with claims of P6,749, P6,000 and P15. The filter runs
    in SQL, so the SQL is where the guard has to be proven.
    """

    def _sql(self, **kw):
        from core import ceo_money_lens as ml
        seen = []

        def _spy(sql):
            seen.append(sql)
            return [], []

        with mock.patch("aware.engine.run_select", side_effect=_spy):
            ml._large_claims(**kw)
        self.assertEqual(len(seen), 1)
        return " ".join(seen[0].split())

    def test_the_query_refuses_anything_under_the_floor(self):
        sql = self._sql()
        # "more than 200,000" - strictly greater, as the CFO said it.
        self.assertIn("nc.reserve_amount > 200000", sql)
        self.assertNotIn("nc.reserve_amount > 0", sql)

    def test_it_returns_every_qualifying_claim_not_just_three(self):
        """He asked for the claims over the floor - all of them. A heading
        that says "claims above P200,000" while the query takes the top 3
        silently drops the fourth in a hail or fleet week."""
        self.assertIn("LIMIT 10", self._sql())

    def test_a_broken_query_is_not_reported_as_a_quiet_week(self):
        """A failure and "nothing qualified" are different facts. Returning []
        for a crash makes the brief tell the CEO there were no large claims
        when in truth nothing was read at all."""
        from core import ceo_money_lens as ml
        with mock.patch("aware.engine.run_select",
                        side_effect=RuntimeError("graphite down")):
            self.assertIsNone(ml._large_claims())

    def test_the_floor_is_two_hundred_thousand_pula(self):
        from core import ceo_money_lens as ml
        self.assertEqual(ml.CLAIM_REPORTING_FLOOR, 200_000)

    def test_a_quiet_week_returns_nothing_rather_than_small_claims(self):
        from core import ceo_money_lens as ml
        with mock.patch("aware.engine.run_select", return_value=([], [])):
            self.assertEqual(ml._large_claims(), [])
