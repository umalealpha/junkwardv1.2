"""Tests ensuring the legacy reinsurer and reinsurance-treaty endpoints are read-only.

These views previously allowed any authenticated user to create, update, or delete
rows, bypassing the approval chain. These tests lock the public API to GET-only
so destructive operations cannot quietly return in a future refactor.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Currency
from reinsurance.models import Reinsurer, ReinsuranceTreaty


class ReinsurerReadOnlyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "ordinary", "ordinary@example.com", "x"
        )
        self.reinsurer = Reinsurer.objects.create(
            name="Munich Re", short_code="MUN"
        )

        self.currency, _ = Currency.objects.get_or_create(code="BWP")
        treaty_type_value = ReinsuranceTreaty._meta.get_field(
            "treaty_type"
        ).choices[0][0]
        self.treaty = ReinsuranceTreaty.objects.create(
            treaty_number="T-001",
            description="Test Treaty",
            reinsurer=self.reinsurer,
            treaty_type=treaty_type_value,
            inception_date=timezone.localdate(),
            expiry_date=timezone.localdate() + timedelta(days=365),
            currency_code=self.currency,
            cession_share_percent=Decimal("10.00"),
        )

        self.client.force_authenticate(user=self.user)

    def test_authenticated_get_on_reinsurers_still_returns_200(self):
        list_url = "/api/v1/reinsurers/"
        detail_url = f"/api/v1/reinsurers/{self.reinsurer.pk}/"

        list_response = self.client.get(list_url)
        detail_response = self.client.get(detail_url)

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)

    def test_write_verbs_on_reinsurers_return_405_and_do_not_change_db(self):
        list_url = "/api/v1/reinsurers/"
        detail_url = f"/api/v1/reinsurers/{self.reinsurer.pk}/"
        original_count = Reinsurer.objects.count()
        data = {"name": "New Re", "short_code": "NEW"}

        self.assertEqual(
            self.client.post(list_url, data, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.put(detail_url, data, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.patch(detail_url, data, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

        self.assertEqual(Reinsurer.objects.count(), original_count)
        self.reinsurer.refresh_from_db()
        self.assertEqual(self.reinsurer.name, "Munich Re")

    def test_delete_existing_reinsurer_does_not_delete_row(self):
        detail_url = f"/api/v1/reinsurers/{self.reinsurer.pk}/"

        response = self.client.delete(detail_url)

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(
            Reinsurer.objects.filter(pk=self.reinsurer.pk).exists()
        )

    def test_write_verbs_on_reinsurance_treaties_return_405_and_do_not_change_db(self):
        list_url = "/api/v1/reinsurance-treaties/"
        detail_url = f"/api/v1/reinsurance-treaties/{self.treaty.pk}/"
        original_count = ReinsuranceTreaty.objects.count()
        data = {"treaty_number": "T-002", "description": "New Treaty"}

        self.assertEqual(
            self.client.post(list_url, data, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.put(detail_url, data, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.patch(detail_url, data, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

        self.assertEqual(ReinsuranceTreaty.objects.count(), original_count)
        self.treaty.refresh_from_db()
        self.assertEqual(self.treaty.treaty_number, "T-001")
        self.assertEqual(self.treaty.description, "Test Treaty")

    def test_anonymous_requests_are_refused(self):
        self.client.force_authenticate(user=None)

        reinsurer_list = self.client.get("/api/v1/reinsurers/")
        treaty_list = self.client.get("/api/v1/reinsurance-treaties/")

        self.assertIn(
            reinsurer_list.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )
        self.assertIn(
            treaty_list.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )