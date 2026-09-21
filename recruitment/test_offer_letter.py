from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, Currency
from hris.models import OfferLetter, OnboardingRequest
from recruitment.models import AuthorityToRecruit


User = get_user_model()


class OfferLetterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code="BWP",
            defaults={"name": "Botswana Pula", "symbol": "P"},
        )
        cls.company = Company.objects.create(code="TST1", name="Test Co One")

        cls.hr_user = User.objects.create_superuser(
            username="hr",
            email="hr@alphadirect.co.bw",
            password="p",
        )
        cls.non_hr_user = User.objects.create_user(
            username="non-hr",
            email="non-hr@alphadirect.co.bw",
            password="p",
        )

        cls.approved = AuthorityToRecruit.objects.create(
            reference="ATR-T1",
            person_name="Test Person",
            position="Analyst",
            department="Finance & Planning",
            entity="Test Co One",
            status="approved",
            kind="recruit",
            quoted_ctc_monthly=Decimal("10000"),
            currency="BWP",
            salary_lines=[{"label": "Basic Salary", "amount": "8000.00"}],
            effective_date=date(2026, 9, 1),
        )

        cls.pending = AuthorityToRecruit.objects.create(
            reference="ATR-P1",
            person_name="Pending Person",
            position="Analyst",
            department="Finance & Planning",
            entity="Test Co One",
            status="pending",
            kind="recruit",
            quoted_ctc_monthly=Decimal("9000"),
        )

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_draft_refused_for_pending_authority(self):
        client = self._client(self.hr_user)
        url = f"/api/v1/recruitment/offers/{self.pending.pk}/draft/"
        response = client.post(url, {"candidate_email": "c@example.com"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(OfferLetter.objects.filter(authority=self.pending).exists())

    def test_docx_downloads(self):
        OfferLetter.objects.create(
            authority=self.approved,
            candidate_email="c@example.com",
            start_date=date(2026, 9, 1),
        )
        client = self._client(self.hr_user)
        url = f"/api/v1/recruitment/offers/{self.approved.pk}/letter/"
        response = client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertTrue(response.content.startswith(b"PK"))

    def test_accepted_without_email_returns_400(self):
        OfferLetter.objects.create(
            authority=self.approved,
            candidate_email="",
            start_date=None,
        )
        client = self._client(self.hr_user)
        url = f"/api/v1/recruitment/offers/{self.approved.pk}/status/"
        response = client.post(
            url,
            {"status": "accepted", "start_date": "2026-09-01"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.approved.offer_letter.refresh_from_db()
        self.assertEqual(self.approved.offer_letter.status, OfferLetter.Status.DRAFT)

    def test_accepted_with_email_and_date_creates_pending_onboarding_request(self):
        OfferLetter.objects.create(
            authority=self.approved,
            candidate_email="",
            start_date=None,
        )
        client = self._client(self.hr_user)
        url = f"/api/v1/recruitment/offers/{self.approved.pk}/status/"
        response = client.post(
            url,
            {
                "status": "accepted",
                "candidate_email": "c@example.com",
                "start_date": "2026-09-01",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.approved.offer_letter.refresh_from_db()
        self.assertEqual(self.approved.offer_letter.status, OfferLetter.Status.ACCEPTED)
        self.assertIsNotNone(self.approved.offer_letter.onboarding_request_id)
        self.assertTrue(
            OnboardingRequest.objects.filter(
                email="c@example.com",
                status="pending",
                full_name="Test Person",
            ).exists()
        )

    def test_non_hr_gets_403(self):
        client = self._client(self.non_hr_user)
        response = client.get("/api/v1/recruitment/offers/")
        self.assertEqual(response.status_code, 403)
