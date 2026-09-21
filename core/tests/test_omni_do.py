"""core/tests/test_omni_do.py — backend actions with no Chrome (CFO 2026-08-12).

`manage.py omni_do` runs an Omni action AS a real user, from the backend. It
must call the SAME service function the in-app button calls (never a raw ORM
write), refuse a payment_request task outright (that close IS the payment
release), resolve --actor to the ONE named user the CFO chose (never picked by
title/role alone — see core/management/commands/omni_do.py's docstring for why),
and write one AuditLog row per successful run.

cfo_approve's own approval rules (segregation of duties, JE posting, status
transitions) are already covered by procurement/tests.py — this file proves
the DISPATCH contract (like core/tests/test_bulk_approve.py does for the
approvals inbox), plus one real end-to-end po_approve round trip so the wrapper
itself is proven, not just mocked.

Run: python manage.py test core.tests.test_omni_do
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import AuditLog, OmniTask, UserProfile
from taskboard.models import CompletionNote

# The one identity --actor cfo resolves to (CFO's choice, 2026-08-17, after
# the tool found two active users both titled CFO on prod and correctly
# refused to guess between them).
CFO_USERNAME = "pganesharajah"


def _cfo_user(username=CFO_USERNAME, title=UserProfile.Title.CFO, active=True):
    u = User.objects.create_user(username=username, password="x", is_active=active)
    prof, _ = UserProfile.objects.get_or_create(user=u)
    prof.title = title
    prof.save()
    return u


class ActorResolutionTests(TestCase):
    def test_no_cfo_user_raises(self):
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--id", "1", "--note", "irrelevant")
        self.assertIn("not found or inactive", str(cm.exception))

    def test_inactive_cfo_not_resolved(self):
        _cfo_user(active=False)
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--id", "1", "--note", "irrelevant")
        self.assertIn("not found or inactive", str(cm.exception))

    def test_wrong_title_refused_not_acted_with_stale_authority(self):
        """The username can resolve while the title no longer matches — e.g.
        the CFO title was moved to someone else. Revert the title re-check in
        omni_do.py and this test goes red (the command would act anyway)."""
        _cfo_user(title=UserProfile.Title.FINANCE_MANAGER)
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--id", "1", "--note", "irrelevant")
        self.assertIn("refusing rather than act", str(cm.exception))
        self.assertFalse(AuditLog.objects.filter(table_name="OmniBackendAction").exists())

    def test_a_second_cfo_titled_user_no_longer_matters(self):
        """Regression guard for the 2026-08-15 finding: a SECOND user also
        holding the CFO title (e.g. excoboard) must not affect resolution —
        --actor cfo is keyed on the named username, not a title scan."""
        _cfo_user()
        _cfo_user(username="excoboard")
        # Should resolve cleanly to pganesharajah and proceed to the next guard
        # (missing task id), not raise an ambiguity error.
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--id", "11111111-1111-1111-1111-111111111111",
                         "--note", "irrelevant")
        self.assertNotIn("Ambiguous", str(cm.exception))

    def test_unknown_action_rejected_by_argparse(self):
        _cfo_user()
        with self.assertRaises(CommandError):
            call_command("omni_do", "--actor", "cfo", "--action", "delete_everything",
                         "--id", "1")


class TaskCloseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cfo = _cfo_user()
        cls.assigner = User.objects.create_user("assigner", password="x")

    def _task(self, source=""):
        return OmniTask.objects.create(
            assigner=self.assigner, assignee=self.cfo,
            title="Review supplier list", source=source,
        )

    def test_closes_a_real_task(self):
        task = self._task()
        call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                     "--id", str(task.pk), "--note", "Reviewed, all correct.")
        task.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.DONE)
        self.assertIsNotNone(task.completed_at)
        note = CompletionNote.objects.get(task=task)
        self.assertEqual(note.body, "Reviewed, all correct.")
        self.assertEqual(note.author, self.cfo)
        log = AuditLog.objects.get(table_name="OmniBackendAction", record_id=str(task.pk))
        self.assertEqual(log.user, self.cfo)
        self.assertIn("task_close", log.description)

    def test_payment_task_refused(self):
        """PAY-DUP-01: closing a payment_request task IS releasing the payment —
        must never happen through the no-Chrome door. Revert this guard in
        core/backend_actions.py and this test goes red."""
        task = self._task(source="payment_request")
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--id", str(task.pk), "--note", "trying to pay via backend")
        self.assertIn("payment", str(cm.exception).lower())
        task.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.PENDING)
        self.assertFalse(AuditLog.objects.filter(
            table_name="OmniBackendAction", record_id=str(task.pk)).exists())

    def test_missing_task_refused(self):
        with self.assertRaises(CommandError):
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--id", "11111111-1111-1111-1111-111111111111", "--note", "does not exist")

    def test_already_done_task_refused(self):
        task = self._task()
        task.status = OmniTask.Status.DONE
        task.save()
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--id", str(task.pk), "--note", "closing again")
        self.assertIn("already completed", str(cm.exception))


class PoApproveDispatchTests(TestCase):
    """Dispatch contract only — mocked, same style as test_bulk_approve.py's
    PoLegDispatch. cfo_approve's own rules have their own suite."""

    @classmethod
    def setUpTestData(cls):
        cls.cfo = _cfo_user()

    def test_calls_cfo_approve_with_resolved_actor_and_writes_audit(self):
        from procurement.models import PurchaseOrder

        class _StubPO:
            pk = 42
            po_number = "PO-0042"

        with patch("procurement.models.PurchaseOrder.objects") as mgr, \
                patch("procurement.services.cfo_approve") as approve:
            mgr.get.return_value = _StubPO()
            call_command("omni_do", "--actor", "cfo", "--action", "po_approve",
                         "--id", "42", "--note", "confirmed with supplier")
        approve.assert_called_once()
        called_po, called_user = approve.call_args[0]
        self.assertIs(called_po.__class__, _StubPO)
        self.assertEqual(called_user, self.cfo)
        log = AuditLog.objects.get(table_name="OmniBackendAction", record_id="42")
        self.assertEqual(log.action, AuditLog.Action.APPROVE)

    def test_service_validation_error_becomes_command_error(self):
        with patch("procurement.models.PurchaseOrder.objects") as mgr, \
                patch("procurement.services.cfo_approve",
                      side_effect=ValidationError("Segregation of duties: ...")):
            mgr.get.return_value = object()
            with self.assertRaises(CommandError) as cm:
                call_command("omni_do", "--actor", "cfo", "--action", "po_approve",
                             "--id", "7", "--note", "x")
        self.assertIn("Segregation", str(cm.exception))
        self.assertFalse(AuditLog.objects.filter(
            table_name="OmniBackendAction", record_id="7").exists())

    def test_missing_po_refused(self):
        with self.assertRaises(CommandError):
            call_command("omni_do", "--actor", "cfo", "--action", "po_approve",
                         "--id", "11111111-1111-1111-1111-111111111111", "--note", "does not exist")


class PoApproveRealRoundTripTest(TestCase):
    """One genuine end-to-end proof (own eyes, not a mock) that omni_do
    actually flips a real PO to APPROVED via the real service function —
    minimal version of the fixture in procurement/tests.py."""

    @classmethod
    def setUpTestData(cls):
        from billing.models import Contact
        from core.models import Company, Currency
        from ledger.models import Account, FiscalPeriod
        from procurement.models import PurchaseOrder, PurchaseOrderLine
        from procurement.services import (
            COMMITMENT_ASSET_CODE, COMMITMENT_LIABILITY_CODE,
            submit_for_approval, fm_approve,
        )

        Currency.objects.get_or_create(
            code="BWP", defaults={"name": "Botswana Pula", "symbol": "P"})
        company = Company.objects.create(code="OMNIDO", name="OmniDo Test Co.")

        # submit_for_approval needs an open period covering the PO's issue
        # date; cfo_approve's commitment JE is dated off cfo_approved_at
        # (today) — mirrors the two-period fixture in procurement/tests.py.
        FiscalPeriod.objects.create(
            period_name="2026-08", start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31), status=FiscalPeriod.Status.OPEN)
        _today = date.today()
        FiscalPeriod.objects.get_or_create(
            period_name=_today.strftime("%Y-%m"),
            defaults={"start_date": date(_today.year, 1, 1),
                     "end_date": date(_today.year, 12, 31),
                     "status": FiscalPeriod.Status.OPEN})

        # cfo_approve posts a commitment JE against these two codes.
        Account.objects.create(code=COMMITMENT_ASSET_CODE,
                               name="Encumbered Purchase Commitments",
                               account_type="asset", sub_type="commitment_reserve",
                               is_active=True)
        Account.objects.create(code=COMMITMENT_LIABILITY_CODE,
                               name="Reserve for Encumbered Commitments",
                               account_type="liability", sub_type="commitment_reserve",
                               is_active=True)

        cls.cfo = _cfo_user()
        submitter = User.objects.create_user("po_submitter", password="x", is_superuser=True)
        fm = User.objects.create_user("po_fm", password="x", is_superuser=True)

        supplier = Contact.objects.create(
            name="Test Supplier", contact_type="vendor",
            is_related_party=False, company=company)
        po = PurchaseOrder.objects.create(
            po_number="PO-OMNIDO-0001", issue_date=date(2026, 8, 1),
            department="admin", supplier=supplier, currency_code_id="BWP",
            exchange_rate=Decimal("1.0"), company=company,
            created_by=submitter, status=PurchaseOrder.Status.DRAFT,
            justification="omni_do round-trip test",
        )
        PurchaseOrderLine.objects.create(
            purchase_order=po, description="Test line",
            quantity=Decimal("1"), unit_price=Decimal("200000.00"),
            line_total=Decimal("200000.00"),
        )
        po.recalculate_totals()
        po.save()
        po = submit_for_approval(po, submitter)
        po = fm_approve(po, fm)
        assert po.status == PurchaseOrder.Status.PENDING_CFO_APPROVAL, po.status
        cls.po_pk = po.pk

    def test_real_po_approved_end_to_end(self):
        from procurement.models import PurchaseOrder

        call_command("omni_do", "--actor", "cfo", "--action", "po_approve",
                     "--id", str(self.po_pk), "--note", "confirmed, approve")
        po = PurchaseOrder.objects.get(pk=self.po_pk)
        self.assertEqual(po.status, PurchaseOrder.Status.APPROVED)
        self.assertEqual(po.cfo_approved_by_id, self.cfo.pk)
        log = AuditLog.objects.get(table_name="OmniBackendAction", record_id=str(self.po_pk))
        self.assertEqual(log.action, AuditLog.Action.APPROVE)
        self.assertIn("PO-OMNIDO-0001", log.description)
