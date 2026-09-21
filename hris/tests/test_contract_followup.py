from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Company, Currency
from hris.contract_followup import (
    digest_html,
    follow_through_renewal,
    orphan_logins,
    probation_due,
    record_probation,
    renewal_letter_docx,
)
from hris.models import (
    ContractRenewalDecision,
    LoginClassification,
    OffboardingCase,
    ProbationDecision,
)
from payroll.models import Employee, EmploymentContract, PayrollPeriod

User = get_user_model()


class ContractFollowupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.currency = Currency.objects.get_or_create(
            code="BWP",
            defaults={"name": "Botswana Pula", "symbol": "P"},
        )[0]
        cls.company = Company.objects.create(code="TST1", name="Test Co One")
        PayrollPeriod.objects.create(
            period_name="2026-08",
            start_date=timezone.datetime(2026, 8, 1).date(),
            end_date=timezone.datetime(2026, 8, 31).date(),
        )

        cls.hr_user = User.objects.create_user(
            username="hruser",
            email="hruser@alphadirect.co.bw",
            password="p",
        )
        cls.head_user = User.objects.create_user(
            username="hrhead",
            email="hrhead@alphadirect.co.bw",
            password="p",
        )

        cls.emp1 = Employee.objects.create(
            employee_number="E001",
            full_name="Emp One",
            email="emp1@example.com",
            company=cls.company,
            status="active",
        )
        cls.emp2 = Employee.objects.create(
            employee_number="E002",
            full_name="Emp Two",
            email="emp2@example.com",
            company=cls.company,
            status="active",
        )

        cls.today = timezone.localdate()
        cls.api = APIClient()
        cls.api.force_authenticate(user=cls.hr_user)

    def _create_contract(self, employee, **kwargs):
        defaults = {
            "start_date": self.today - timedelta(days=100),
            "end_date": self.today + timedelta(days=265),
            "probation_end_date": self.today + timedelta(days=20),
            "contract_type": "permanent",
            "status": "active",
            "basic": Decimal("10000.00"),
        }
        defaults.update(kwargs)
        return EmploymentContract.objects.create(employee=employee, **defaults)

    def test_probation_due_picks_20_days_not_40(self):
        c20 = self._create_contract(
            self.emp1,
            probation_end_date=self.today + timedelta(days=20),
        )
        c40 = self._create_contract(
            self.emp2,
            probation_end_date=self.today + timedelta(days=40),
        )

        due = probation_due(self.today)

        self.assertIn(c20, due)
        self.assertNotIn(c40, due)

    @patch("hris.contract_followup_views.user_can_access_hris", return_value=True)
    def test_extend_beyond_6_months_returns_400(self, _mock_access):
        contract = self._create_contract(
            self.emp1,
            start_date=self.today - timedelta(days=140),
            end_date=self.today + timedelta(days=365),
            probation_end_date=self.today + timedelta(days=20),
            contract_type="probation",
        )
        new_end = (self.today + timedelta(days=200)).isoformat()

        response = self.api.post(
            f"/api/v1/hris/contracts/{contract.id}/probation/",
            {"decision": "extend", "new_end": new_end},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    @patch("hris.contract_followup_views.user_can_access_hris", return_value=True)
    def test_confirm_flips_probation_to_permanent(self, _mock_access):
        contract = self._create_contract(
            self.emp1,
            contract_type="probation",
        )

        response = self.api.post(
            f"/api/v1/hris/contracts/{contract.id}/probation/",
            {"decision": "confirm"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        contract.refresh_from_db()
        self.assertEqual(contract.contract_type, "permanent")
        self.assertTrue(
            ProbationDecision.objects.filter(contract=contract, decision="confirm").exists()
        )

    def test_renew_same_creates_pending_next_contract_same_length_capped(self):
        start = self.today - timedelta(days=300)
        end = self.today + timedelta(days=65)
        old = self._create_contract(
            self.emp1,
            start_date=start,
            end_date=end,
            contract_type="fixed_term",
            status="active",
        )
        decision = ContractRenewalDecision.objects.create(
            contract=old,
            decision="renew_same",
            decided_by=self.hr_user,
        )

        result = follow_through_renewal(decision, self.hr_user)

        self.assertIn("new_contract_id", result)
        new_contract = EmploymentContract.objects.get(pk=result["new_contract_id"])
        self.assertEqual(new_contract.status, "pending")
        self.assertEqual(new_contract.employee_id, self.emp1.pk)
        self.assertEqual(new_contract.start_date, old.end_date + timedelta(days=1))

        from hris.contract_module import add_months

        cap_end = add_months(new_contract.start_date, 12) - timedelta(days=1)
        self.assertLessEqual(new_contract.end_date, cap_end)
        self.assertEqual(
            new_contract.end_date,
            min(new_contract.start_date + (old.end_date - old.start_date), cap_end),
        )

    def test_end_opens_offboarding_case(self):
        old = self._create_contract(
            self.emp1,
            end_date=self.today + timedelta(days=10),
        )
        decision = ContractRenewalDecision.objects.create(
            contract=old,
            decision="end",
            decided_by=self.hr_user,
        )

        result = follow_through_renewal(decision, self.hr_user)

        self.assertIn("offboarding_case_id", result)
        case = OffboardingCase.objects.get(pk=result["offboarding_case_id"])
        self.assertEqual(case.employee_id, self.emp1.pk)
        self.assertEqual(case.reason, "end_of_contract")
        self.assertEqual(case.last_working_day, old.end_date)

    def test_renewal_letter_docx_starts_with_pk(self):
        contract = self._create_contract(self.emp1)

        docx_bytes = renewal_letter_docx(contract)

        self.assertTrue(docx_bytes.startswith(b"PK"))

    def test_orphan_logins_lists_unlinked_login_and_excludes_email_linked(self):
        orphan_user = User.objects.create_user(
            username="orphan1",
            email="orphan1@example.com",
            password="p",
        )
        staff_user = User.objects.create_user(
            username="staff2",
            email="staff2@example.com",
            password="p",
        )
        Employee.objects.create(
            employee_number="E003",
            full_name="Staff Two",
            email="staff2@example.com",
            company=self.company,
            status="active",
        )

        rows = orphan_logins()
        user_ids = {row["user_id"] for row in rows}

        self.assertIn(orphan_user.pk, user_ids)
        self.assertNotIn(staff_user.pk, user_ids)

    @patch("hris.contract_followup_views.user_can_access_hris", return_value=True)
    @patch("hris.contract_followup_views.is_hr_head", return_value=True)
    @patch("core.session_teardown.revoke_device_sessions")
    @patch("core.session_teardown.clear_browser_tokens")
    def test_classify_close_deactivates_login(
        self,
        _clear_mock,
        _revoke_mock,
        _head_mock,
        _access_mock,
    ):
        victim = User.objects.create_user(
            username="victim",
            email="victim@example.com",
            password="p",
        )

        response = self.api.post(
            f"/api/v1/hris/logins-without-payroll/{victim.pk}/classify/",
            {"kind": "close"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        victim.refresh_from_db()
        self.assertFalse(victim.is_active)
        self.assertTrue(
            LoginClassification.objects.filter(user=victim, kind="close").exists()
        )
        _revoke_mock.assert_called_once()
        _clear_mock.assert_called_once()

    @patch("hris.contract_followup_views.user_can_access_hris", return_value=True)
    @patch("hris.contract_followup_views.is_hr_head", return_value=False)
    def test_classify_non_head_403(self, _head_mock, _access_mock):
        victim = User.objects.create_user(
            username="victim2",
            email="victim2@example.com",
            password="p",
        )

        response = self.api.post(
            f"/api/v1/hris/logins-without-payroll/{victim.pk}/classify/",
            {"kind": "close"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_digest_subject_contains_hr(self):
        subject, _html = digest_html(self.today)

        self.assertIn("HR", subject)

    @patch("hris.management.commands.hr_monday_digest.send_html_with_cfo_cc")
    def test_command_dry_run_sends_nothing(self, send_mock):
        out = StringIO()

        call_command("hr_monday_digest", stdout=out)

        self.assertFalse(send_mock.called)
        self.assertIn("Dry run", out.getvalue())

    def test_classify_rejects_unknown_kind(self):
        from unittest.mock import patch as _p
        from django.contrib.auth import get_user_model
        target = get_user_model().objects.create_user(username='orph', email='orph@example.com')
        with _p('hris.contract_followup_views.is_hr_head', return_value=True), \
                _p('hris.contract_followup_views.user_can_access_hris', return_value=True):
            r = self.api.post(f'/api/v1/hris/logins-without-payroll/{target.pk}/classify/',
                              {'kind': 'nonsense-kind'}, format='json')
        self.assertEqual(r.status_code, 400)

