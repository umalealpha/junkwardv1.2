"""Regression tests for the Fable review fixes (2026-07-13):
  1. Incentive sign-off — dual control (CFO directive 2026-07-23, superseding the
     2026-07-15 single-CFO arrangement): BOTH the CFO and HR (Unami) must sign,
     from two different people, before an incentive is approved. Maker != approver
     still holds.
  2. AP-aging upload/snapshot are finance-only.
  3. Expense-claim files download only for people on the claim.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import UserProfile


def _profile(user, title=None) -> UserProfile:
    p, _ = UserProfile.objects.get_or_create(user=user)
    if title:
        p.title = title
        p.save(update_fields=["title"])
    return p
from hris.expense_claim_models import ExpenseClaim
from hris.incentive_models import IncentiveRequest
from hris.incentive_service import approve_request


class DistinctSignersTests(APITestCase):
    def setUp(self):
        self.maker = User.objects.create_user("maker", password="x")
        self.superuser = User.objects.create_superuser("root", "r@x.com", "x")
        self.req = IncentiveRequest.objects.create(
            title="July incentives", period="2026-07", maker=self.maker)

    def test_both_signatures_approve(self):
        # CFO directive 2026-07-23 (supersedes 2026-07-15): an incentive needs
        # BOTH the CFO and the HR signature — from two different people — before
        # it is approved. One signature alone leaves it pending.
        approve_request(self.req, self.superuser, requested_slot="cfo")
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.cfo_approved_at)
        self.assertEqual(self.req.status, IncentiveRequest.Status.PENDING)

        other = User.objects.create_superuser("root2", "r2@x.com", "x")
        approve_request(self.req, other, requested_slot="hr")
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.hr_approved_at)
        self.assertEqual(self.req.status, IncentiveRequest.Status.APPROVED)

    def test_same_person_cannot_sign_both_legs(self):
        # Dual control is two HUMANS, not two slots — one superuser must not be
        # able to sign both the CFO and the HR leg.
        approve_request(self.req, self.superuser, requested_slot="cfo")
        with self.assertRaises(ValidationError):
            approve_request(self.req, self.superuser, requested_slot="hr")

    def test_further_signoff_after_approval_is_refused(self):
        approve_request(self.req, self.superuser, requested_slot="cfo")
        other = User.objects.create_superuser("root2", "r2@x.com", "x")
        approve_request(self.req, other, requested_slot="hr")
        # Fully approved now — any further sign-off is refused.
        third = User.objects.create_superuser("root3", "r3@x.com", "x")
        with self.assertRaises(ValidationError):
            approve_request(self.req, third, requested_slot="cfo")

    def test_maker_cannot_approve_own(self):
        # Segregation of duties still holds.
        self.req.maker = self.superuser
        self.req.save(update_fields=["maker"])
        with self.assertRaises(ValidationError):
            approve_request(self.req, self.superuser, requested_slot="cfo")


class APAgingGateTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user("plain", password="x")

    def test_snapshot_forbidden_for_non_finance(self):
        self.client.force_authenticate(user=self.staff)
        r = self.client.get(reverse("v1-ap-aging-snapshot"))
        self.assertEqual(r.status_code, 403)

    def test_upload_forbidden_for_non_finance(self):
        self.client.force_authenticate(user=self.staff)
        f = SimpleUploadedFile("a.csv", b"Vendor,Total\nAcme,100\n")
        r = self.client.post(reverse("v1-ap-aging-upload"),
                             {"file": f, "as_of": "2026-06-30"}, format="multipart")
        self.assertEqual(r.status_code, 403)

    def test_snapshot_ok_for_finance(self):
        fm = User.objects.create_user("fm", password="x")
        _profile(fm, UserProfile.Title.FINANCE_MANAGER)
        self.client.force_authenticate(user=fm)
        r = self.client.get(reverse("v1-ap-aging-snapshot"))
        self.assertEqual(r.status_code, 200)

    def test_bad_company_uuid_is_400_not_500(self):
        root = User.objects.create_superuser("root3", "r3@x.com", "x")
        self.client.force_authenticate(user=root)
        r = self.client.get(reverse("v1-ap-aging-snapshot") + "?company=not-a-uuid")
        self.assertEqual(r.status_code, 400)


class ExpenseFileAccessTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="x")
        self.stranger = User.objects.create_user("stranger", password="x")
        self.claim = ExpenseClaim.objects.create(
            profile=_profile(self.owner), expense_date="2026-07-01",
            amount="100.00", status=ExpenseClaim.Status.PENDING_CFO,
            payment_proof=SimpleUploadedFile("proof.pdf", b"%PDF-1.4 proof"))
        self.url = reverse("v1-expense-file", args=[self.claim.id])

    def test_stranger_forbidden(self):
        self.client.force_authenticate(user=self.stranger)
        self.assertEqual(self.client.get(self.url + "?kind=proof").status_code, 403)

    def test_owner_can_download(self):
        self.client.force_authenticate(user=self.owner)
        r = self.client.get(self.url + "?kind=proof")
        self.assertEqual(r.status_code, 200)
