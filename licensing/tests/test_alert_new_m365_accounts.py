from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from core.models import Company, Currency
from licensing.models import M365AccountSeen
from payroll.models import Employee

MODULE = "licensing.management.commands.alert_new_m365_accounts"
HR_RECIPIENTS = [
    "dikgopoleng@alphadirect.co.bw",
    "ubutale@alphadirect.co.bw",
]


def graph_account(
    object_id,
    email,
    display_name="Name",
    job_title="",
    department="",
):
    return {
        "object_id": object_id,
        "display_name": display_name,
        "user_principal_name": email,
        "email": email,
        "job_title": job_title,
        "department": department,
        "license_count": 1,
        "last_interactive_signin_at": None,
    }


class AlertNewM365AccountsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code="BWP",
            defaults={"name": "Botswana Pula", "symbol": "P"},
        )
        company = Company.objects.create(code="TST1", name="Test Co One")
        Employee.objects.create(
            employee_number="E001",
            full_name="Existing Payroll",
            email="new@alphadirect.co.bw",
            company=company,
        )

    @patch(MODULE + ".send_html_with_cfo_cc", return_value=1)
    @patch(MODULE + ".fetch_active_licensed_humans")
    def test_first_run_seeds_everything_and_sends_nothing(
        self, mock_fetch, mock_send
    ):
        mock_fetch.return_value = [
            graph_account("id1", "a@alphadirect.co.bw", "Alpha"),
            graph_account("id2", "agent@insurance.co.bw", "Agent"),
        ]

        call_command("alert_new_m365_accounts", "--commit")

        self.assertEqual(M365AccountSeen.objects.count(), 2)
        self.assertTrue(all(row.alerted for row in M365AccountSeen.objects.all()))
        self.assertTrue(
            M365AccountSeen.objects.get(object_id="id2").is_agent
        )
        mock_send.assert_not_called()

    @patch(MODULE + ".send_html_with_cfo_cc", return_value=1)
    @patch(MODULE + ".fetch_active_licensed_humans")
    def test_new_alphadirect_account_sends_one_email(
        self, mock_fetch, mock_send
    ):
        existing = graph_account(
            "id1", "existing@alphadirect.co.bw", "Existing"
        )
        M365AccountSeen.objects.create(
            object_id="id1",
            email="existing@alphadirect.co.bw",
            display_name="Existing",
            alerted=True,
            is_agent=False,
        )
        new = graph_account(
            "id2",
            "new@alphadirect.co.bw",
            "New Person",
            job_title="Analyst",
            department="Risk",
        )
        mock_fetch.return_value = [existing, new]

        call_command("alert_new_m365_accounts", "--commit")

        self.assertEqual(M365AccountSeen.objects.count(), 2)
        row = M365AccountSeen.objects.get(object_id="id2")
        self.assertTrue(row.alerted)
        self.assertFalse(row.is_agent)

        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["subject"], "New Microsoft account: New Person")
        self.assertEqual(kwargs["to"], HR_RECIPIENTS)
        self.assertTrue(kwargs["cc_cfo"])
        self.assertIn("New Person", kwargs["html"])
        self.assertIn("new@alphadirect.co.bw", kwargs["html"])
        self.assertIn("Analyst", kwargs["html"])
        self.assertIn("Risk", kwargs["html"])
        self.assertIn("Yes", kwargs["html"])

    @patch(MODULE + ".send_html_with_cfo_cc", return_value=1)
    @patch(MODULE + ".fetch_active_licensed_humans")
    def test_insurance_agent_is_recorded_without_email(
        self, mock_fetch, mock_send
    ):
        existing = graph_account(
            "id1", "existing@alphadirect.co.bw", "Existing"
        )
        M365AccountSeen.objects.create(
            object_id="id1",
            email="existing@alphadirect.co.bw",
            display_name="Existing",
            alerted=True,
            is_agent=False,
        )
        agent = graph_account(
            "id2", "agent@insurance.co.bw", "Agent"
        )
        mock_fetch.return_value = [existing, agent]

        call_command("alert_new_m365_accounts", "--commit")

        self.assertEqual(M365AccountSeen.objects.count(), 2)
        row = M365AccountSeen.objects.get(object_id="id2")
        self.assertTrue(row.is_agent)
        self.assertFalse(row.alerted)
        mock_send.assert_not_called()

    @patch(MODULE + ".send_html_with_cfo_cc", return_value=1)
    @patch(MODULE + ".fetch_active_licensed_humans")
    def test_identical_run_sends_nothing(self, mock_fetch, mock_send):
        accounts = [
            graph_account("id1", "existing@alphadirect.co.bw", "Existing"),
            graph_account("id2", "new@alphadirect.co.bw", "New"),
        ]
        for account in accounts:
            M365AccountSeen.objects.create(
                object_id=account["object_id"],
                email=account["email"],
                display_name=account["display_name"],
                alerted=True,
                is_agent=False,
            )
        mock_fetch.return_value = accounts

        call_command("alert_new_m365_accounts", "--commit")

        self.assertEqual(M365AccountSeen.objects.count(), 2)
        mock_send.assert_not_called()

    @patch(MODULE + ".send_html_with_cfo_cc", return_value=1)
    @patch(MODULE + ".fetch_active_licensed_humans")
    def test_dry_run_writes_nothing(self, mock_fetch, mock_send):
        existing = graph_account(
            "id1", "existing@alphadirect.co.bw", "Existing"
        )
        M365AccountSeen.objects.create(
            object_id="id1",
            email="existing@alphadirect.co.bw",
            display_name="Existing",
            alerted=True,
            is_agent=False,
        )
        new = graph_account(
            "id2", "new@alphadirect.co.bw", "New"
        )
        mock_fetch.return_value = [existing, new]

        call_command("alert_new_m365_accounts")

        self.assertEqual(M365AccountSeen.objects.count(), 1)
        mock_send.assert_not_called()
