"""
Batch-3 tests for the Staff Incentives module (CFO 2026-07-22):

  AMEND
    * an authorised person corrects a still-pending request's amount, and the
      old→new figures are written to the immutable AuditLog;
    * when the amount changes, any partial CFO/HR signature is cleared and the
      request drops back to PENDING (a changed figure can never carry an old
      approval); a non-amount edit keeps the signature;
    * amend is blocked once the request is processed in payroll, and blocked for
      anyone who is neither the requester nor an approver;
    * amend re-runs the earned-incentive justification gate.

  RECURRING
    * generation creates exactly one request per ACTIVE template for a period
      and is idempotent — re-running does not double-create; a fresh period
      generates again.

NOTE: hris migration 0049 depends on 0048_disciplinaryattachment (owned by a
parallel batch-3 agent). These tests can only be executed once 0048 is present
in the tree; the logic under test is self-contained here.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import AuditLog
from hris.incentive_models import (
    IncentiveLine, IncentiveRequest, RecurringIncentive,
)
from hris.incentive_service import (
    amend_incentive, approve_request, create_recurring_template,
    generate_recurring_for_period, set_recurring_active,
)

_GOOD_WHY = ("This work went well beyond her normal day-to-day duties and required "
             "significant additional effort across the whole period to plan, execute "
             "and complete correctly. It was delivered on time, was completely "
             "error-free, and needed no manager intervention at all. The result was "
             "clearly over and above what her role ordinarily requires of her each "
             "month")


def _make_request(maker, *, amount="10000.00", period="2026-07",
                  name="Alice") -> IncentiveRequest:
    req = IncentiveRequest.objects.create(
        title="Motor claims incentives", period=period,
        maker=maker, maker_email=getattr(maker, "email", "") or "")
    IncentiveLine.objects.create(
        request=req, name=name, amount=Decimal(amount),
        beyond_normal_duties=True, on_time=True, error_free=True,
        needed_manager_fix=False, justification=_GOOD_WHY)
    return req


class AmendTests(TestCase):
    def setUp(self):
        self.maker = User.objects.create_user("maker", "maker@x.com", "x")
        self.cfo = User.objects.create_superuser("root", "r@x.com", "x")
        # Second distinct approver for the HR leg (dual control, CFO 2026-07-23).
        self.hr = User.objects.create_superuser("hruser", "hr@x.com", "x")
        self.stranger = User.objects.create_user("stranger", "s@x.com", "x")

    def test_amend_changes_amount_and_records_audit(self):
        req = _make_request(self.maker, amount="10000.00")

        amend_incentive(req.id, self.maker, amount="1000.00",
                        reason="Fat-fingered 10,000 instead of 1,000.")

        req.refresh_from_db()
        line = req.lines.get()
        self.assertEqual(line.amount, Decimal("1000.00"))
        self.assertEqual(req.total, Decimal("1000.00"))
        self.assertIsNotNone(req.amended_at)
        self.assertEqual(req.amended_by_id, self.maker.pk)

        entry = AuditLog.objects.filter(
            table_name="IncentiveRequest", record_id=str(req.pk),
            description__icontains="amended").order_by("-created_at").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.user_id, self.maker.pk)
        self.assertEqual(entry.old_values["total"], "10000.00")
        self.assertEqual(entry.new_values["total"], "1000.00")

    def test_amend_resets_signatures_when_amount_changes(self):
        req = _make_request(self.maker, amount="10000.00")
        # CFO + HR both sign → APPROVED (dual control, CFO 2026-07-23).
        approve_request(req, self.cfo, requested_slot="cfo")
        approve_request(req, self.hr, requested_slot="hr")
        req.refresh_from_db()
        self.assertEqual(req.status, IncentiveRequest.Status.APPROVED)
        self.assertIsNotNone(req.cfo_approved_at)
        self.assertIsNotNone(req.hr_approved_at)

        # Requester corrects the amount → BOTH sign-offs must be cleared.
        amend_incentive(req.id, self.maker, amount="1000.00")

        req.refresh_from_db()
        self.assertEqual(req.status, IncentiveRequest.Status.PENDING)
        self.assertIsNone(req.cfo_approved_at)
        self.assertIsNone(req.cfo_approver_id)
        self.assertIsNone(req.hr_approved_at)
        self.assertIsNone(req.hr_approver_id)
        self.assertEqual(req.lines.get().amount, Decimal("1000.00"))

    def test_amend_without_amount_change_keeps_signature(self):
        req = _make_request(self.maker, amount="10000.00")
        approve_request(req, self.cfo, requested_slot="cfo")
        approve_request(req, self.hr, requested_slot="hr")

        # Only the notes change — the amount (and both approvals) stand.
        amend_incentive(req.id, self.cfo, notes="Loaded to FNB batch 42.")

        req.refresh_from_db()
        self.assertEqual(req.status, IncentiveRequest.Status.APPROVED)
        self.assertIsNotNone(req.cfo_approved_at)
        self.assertIsNotNone(req.hr_approved_at)
        self.assertEqual(req.notes, "Loaded to FNB batch 42.")

    def test_amend_blocked_once_processed(self):
        req = _make_request(self.maker, amount="10000.00")
        req.payroll_processed = True
        req.save(update_fields=["payroll_processed"])

        with self.assertRaises(ValidationError):
            amend_incentive(req.id, self.maker, amount="1000.00")

        req.refresh_from_db()
        self.assertEqual(req.lines.get().amount, Decimal("10000.00"))

    def test_amend_blocked_for_unauthorised_user(self):
        req = _make_request(self.maker, amount="10000.00")
        with self.assertRaises(ValidationError):
            amend_incentive(req.id, self.stranger, amount="1000.00")

    def test_amend_reruns_justification_gate(self):
        req = _make_request(self.maker, amount="10000.00")
        line = req.lines.get()
        with self.assertRaises(ValidationError):
            amend_incentive(
                req.id, self.maker,
                lines=[{"id": str(line.pk), "justification": "too short"}])


class RecurringTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss", "boss@x.com", "x")

    def _templates(self):
        create_recurring_template(user=self.boss, name="Alice", amount="500",
                                  category="Sales incentive")
        create_recurring_template(user=self.boss, name="Bob", amount="750",
                                  category="Support incentive")
        inactive = create_recurring_template(user=self.boss, name="Old Hand",
                                             amount="100", category="Legacy")
        set_recurring_active(inactive.id, self.boss, active=False)

    def test_generates_one_request_per_active_template(self):
        self._templates()
        self.assertEqual(RecurringIncentive.objects.filter(active=True).count(), 2)

        result = generate_recurring_for_period("2026-08", self.boss)

        self.assertEqual(result["created_count"], 2)          # only the active two
        made = IncentiveRequest.objects.filter(
            period="2026-08", source_template__isnull=False)
        self.assertEqual(made.count(), 2)
        for req in made:
            self.assertEqual(req.lines.count(), 1)
            self.assertEqual(req.status, IncentiveRequest.Status.PENDING)
            self.assertEqual(req.lines.get().amount, req.source_template.amount)

    def test_generation_is_idempotent(self):
        self._templates()
        first = generate_recurring_for_period("2026-08", self.boss)
        self.assertEqual(first["created_count"], 2)

        again = generate_recurring_for_period("2026-08", self.boss)
        self.assertEqual(again["created_count"], 0)
        self.assertEqual(again["skipped_count"], 2)

        # Same period never double-creates …
        self.assertEqual(
            IncentiveRequest.objects.filter(period="2026-08").count(), 2)
        # … but a new period generates afresh.
        nxt = generate_recurring_for_period("2026-09", self.boss)
        self.assertEqual(nxt["created_count"], 2)

    def test_generated_request_is_amendable(self):
        self._templates()
        generate_recurring_for_period("2026-08", self.boss)
        req = IncentiveRequest.objects.filter(period="2026-08").first()

        # The synthesised standing justification satisfies the gate on amend.
        amend_incentive(req.id, self.boss, amount="1234.00")
        req.refresh_from_db()
        self.assertEqual(req.lines.get().amount, Decimal("1234.00"))
