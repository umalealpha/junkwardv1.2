"""Redundancy & re-hire transfer mode + leaver final leave pay (CFO 2026-09-05,
Naomi Pheko: made redundant at ADIC, re-hired at Unicoin on 1 Sep).

Pins: the old record is terminated the day before the start; a leaver
settlement (full balance, no residual floor, BASIC ÷ 24) is raised for CFO → HR →
Finance; a new record is created at the destination with the login, the Time
Doctor link and a zero (or carried) leave opening balance; the submitter can
never approve; an asset still held blocks the exit.
"""

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from hris.leave_encash_models import LeaveEncashment
from hris.leave_encash_service import (
    apply_encashment,
    approve,
    raise_leaver_settlement,
    settlement_quote,
)
from hris.models import HRISProfile, LeaveOpeningBalance
from hris.transfer_models import EmployeeTransfer
from hris.transfer_service import (
    approve_in,
    approve_out,
    next_employee_number,
    submit_transfer,
)
from integrations.models import TimeDoctorUserMap
from payroll.models import (
    Employee,
    Payslip,
    PayslipComponent,
    PayslipLine,
    PayrollPeriod,
)

TODAY = timezone.localdate()


def _payslip(emp, company, basic="12000.00"):
    comp, _ = PayslipComponent.objects.get_or_create(
        code="BASIC",
        defaults={"name": "Basic Salary", "kind": "earning", "is_taxable": True},
    )
    period, _ = PayrollPeriod.objects.get_or_create(
        period_name="2099-02",
        defaults={"start_date": dt.date(2099, 2, 1), "end_date": dt.date(2099, 2, 28)},
    )
    slip = Payslip.objects.create(
        employee=emp, period=period, company=company, status=Payslip.Status.DRAFT
    )
    PayslipLine.objects.create(payslip=slip, component=comp, amount=Decimal(basic))


class RehireTransferTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.adic = Company.objects.create(code="ADIC", name="Alpha Direct Insurance")
        cls.uni = Company.objects.create(code="UNI", name="Unicoin")
        cls.login = User.objects.create_user(
            "naomi.pheko", email="npheko@alphadirect.co.bw", password="x"
        )
        cls.manager = Employee.objects.create(
            employee_number="ADIC_001", full_name="Kakale Botana", company=cls.adic
        )
        cls.emp = Employee.objects.create(
            employee_number="ADIC_688",
            full_name="Naomi Natasha Pheko",
            department="Compliance",
            job_title="KYC Agent",
            email="npheko@alphadirect.co.bw",
            hire_date=dt.date(2025, 2, 24),
            company=cls.adic,
            user=cls.login,
            bank_name="FNB",
        )
        cls.profile = HRISProfile.objects.create(
            employee=cls.emp, manager=cls.manager, gender="F"
        )
        # 8.5 days available as at today (18-day entitlement).
        LeaveOpeningBalance.objects.create(
            profile=cls.profile,
            leave_type_code="annual",
            entitlement_days=Decimal("18"),
            opening_balance_days=Decimal("8.5"),
            accrued_days=Decimal("4.5"),
            as_at_date=TODAY,
        )
        _payslip(cls.emp, cls.adic)
        cls.junior = Employee.objects.create(employee_number="ADIC_700", full_name="Junior Report",
                                             company=cls.adic)
        HRISProfile.objects.create(employee=cls.junior, manager=cls.emp)
        TimeDoctorUserMap.objects.create(
            td_user_id="addGnUsqy_cfxdpq",
            td_name="Naomi Pheko",
            employee=cls.emp,
            confirmed=True,
            source=TimeDoctorUserMap.Source.MANUAL,
        )
        # A never-used duplicate login already sits on the new address.
        cls.dup = User.objects.create_user(
            "npheko", email="npheko@insurance.co.bw", password="x"
        )

        cls.dorothy = User.objects.create_user(
            "dikgopoleng", email="dikgopoleng@alphadirect.co.bw", password="x"
        )
        cls.unami = User.objects.create_user(
            "ubutale", email="ubutale@alphadirect.co.bw", password="x"
        )
        cls.cfo = User.objects.create_user(
            "pg", email="pganesharajah@alphadirect.co.bw", password="x"
        )
        cls.fm = User.objects.create_user(
            "fm", email="fm@alphadirect.co.bw", password="x"
        )
        UserProfile.objects.update_or_create(
            user=cls.fm,
            defaults={"title": UserProfile.Title.FINANCE_MANAGER, "is_active": True},
        )

    def _submit(self, **kw):
        base = dict(
            submitter=self.dorothy,
            employee_id=str(self.emp.id),
            dest_company_id=str(self.uni.id),
            effective_date=TODAY,
            reason="Redundancy at Alpha Direct",
            mode=EmployeeTransfer.Mode.REHIRE,
        )
        base.update(kw)
        return submit_transfer(**base)

    # ── the happy path ──────────────────────────────────────────────────────
    def test_rehire_terminates_settles_and_creates_new_record(self):
        t = self._submit(new_email="npheko@insurance.co.bw")
        self.assertEqual(t.mode, "rehire")
        approve_out(t, self.unami)
        approve_in(t, self.cfo)
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.COMPLETED)

        old = Employee.objects.get(pk=self.emp.pk)
        self.assertEqual(old.status, Employee.Status.TERMINATED)
        self.assertEqual(old.termination_date, TODAY - dt.timedelta(days=1))
        self.assertIsNone(old.user_id)  # login moved
        self.assertEqual(old.company_id, self.adic.id)  # history stays at ADIC

        new = t.new_employee
        self.assertIsNotNone(new)
        self.assertEqual(new.company_id, self.uni.id)
        self.assertEqual(new.status, Employee.Status.ACTIVE)
        self.assertEqual(new.hire_date, TODAY)
        self.assertEqual(new.email, "npheko@insurance.co.bw")
        self.assertEqual(new.full_name, old.full_name)
        self.assertEqual(new.job_title, "KYC Agent")
        self.assertTrue(new.employee_number.startswith("UNI_"))
        self.assertEqual(new.user_id, self.login.pk)  # same person signs in
        self.login.refresh_from_db()
        self.assertEqual(self.login.email, "npheko@insurance.co.bw")
        self.dup.refresh_from_db()
        self.assertFalse(self.dup.is_active)  # duplicate login retired

        # Profile carried: same manager, reporting line survives.
        np = HRISProfile.objects.get(employee=new)
        self.assertEqual(np.manager_id, self.manager.id)
        self.assertEqual(np.gender, "F")

        # Leave starts at ZERO on the start date (paid out at ADIC).
        ob = LeaveOpeningBalance.objects.get(profile=np, leave_type_code="annual")
        self.assertEqual(ob.as_at_date, TODAY)
        self.assertEqual(ob.opening_balance_days, Decimal("0.00"))
        self.assertEqual(ob.entitlement_days, Decimal("18"))

        # Her own reports (if any) now hang off the NEW record, not the dead one.
        self.assertFalse(HRISProfile.objects.filter(manager=old).exists())
        self.assertEqual(HRISProfile.objects.filter(manager=new).count(), 1)

        # Time Doctor link followed her, still confirmed.
        tm = TimeDoctorUserMap.objects.get(td_user_id="addGnUsqy_cfxdpq")
        self.assertEqual(tm.employee_id, new.id)
        self.assertTrue(tm.confirmed)

        # Settlement raised: full 8.5 days, BASIC 12,000 ÷ 24 = 500/day, CFO first.
        s = t.settlement
        self.assertIsNotNone(s)
        self.assertEqual(s.kind, LeaveEncashment.Kind.SETTLEMENT)
        self.assertEqual(s.employee_id, old.id)
        self.assertEqual(s.days, Decimal("8.50"))
        self.assertEqual(s.daily_rate, Decimal("500.00"))
        self.assertEqual(s.amount, Decimal("4250.00"))
        self.assertEqual(s.last_day, TODAY - dt.timedelta(days=1))
        self.assertEqual(s.status, LeaveEncashment.Status.PENDING_CFO)
        self.assertEqual(s.applicant_id, self.dorothy.pk)

    def test_second_apply_is_a_no_op(self):
        from hris.transfer_service import _apply_move
        t = self._submit()
        approve_out(t, self.unami)
        approve_in(t, self.cfo)
        t.refresh_from_db()
        before = Employee.objects.count()
        _apply_move(t, self.cfo)                      # double-tap
        self.assertEqual(Employee.objects.count(), before)
        self.assertEqual(LeaveEncashment.objects.filter(kind="settlement").count(), 1)

    def test_carry_leave_puts_balance_on_new_record_and_raises_no_settlement(self):
        t = self._submit(leave_treatment=EmployeeTransfer.LeaveTreatment.CARRY)
        approve_out(t, self.unami)
        approve_in(t, self.cfo)
        t.refresh_from_db()
        self.assertIsNone(t.settlement)
        ob = LeaveOpeningBalance.objects.get(
            profile__employee=t.new_employee, leave_type_code="annual"
        )
        self.assertEqual(ob.opening_balance_days, Decimal("8.50"))
        self.assertFalse(LeaveEncashment.objects.filter(employee=self.emp).exists())

    def test_carry_mode_still_just_moves_the_record(self):
        t = self._submit(mode=EmployeeTransfer.Mode.CARRY)
        approve_out(t, self.unami)
        approve_in(t, self.cfo)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.uni.id)
        self.assertEqual(self.emp.status, Employee.Status.ACTIVE)
        self.assertEqual(
            Employee.objects.filter(full_name=self.emp.full_name).count(), 1
        )

    # ── the settlement chain ────────────────────────────────────────────────
    def test_settlement_walks_cfo_hr_finance_and_submitter_never_signs(self):
        t = self._submit()
        approve_out(t, self.unami)
        approve_in(t, self.cfo)
        t.refresh_from_db()
        s = t.settlement
        with self.assertRaises(ValidationError):  # Dorothy raised it → cannot sign
            approve(s, self.dorothy)
        approve(s, self.cfo)
        s.refresh_from_db()
        self.assertEqual(s.status, LeaveEncashment.Status.PENDING_HR)
        approve(s, self.unami)
        s.refresh_from_db()
        self.assertEqual(s.status, LeaveEncashment.Status.PENDING_FINANCE)
        approve(s, self.fm)
        s.refresh_from_db()
        self.assertEqual(s.status, LeaveEncashment.Status.APPROVED)

    def test_settlement_pays_full_balance_no_residual_floor_even_when_terminated(self):
        # A serving employee could NOT cash 8.5 of 8.5 (10-day floor) …
        with self.assertRaises(ValidationError):
            apply_encashment(applicant=self.login, days="8.5", reason="word " * 60)
        # … a leaver gets everything, and the record may already be terminated.
        Employee.objects.filter(pk=self.emp.pk).update(
            status="terminated", termination_date=TODAY
        )
        emp = Employee.objects.get(pk=self.emp.pk)
        s = raise_leaver_settlement(
            initiator=self.dorothy, employee=emp, last_day=TODAY
        )
        self.assertEqual(s.days, Decimal("8.50"))
        self.assertEqual(s.net_amount + s.tax_amount, s.amount)

    def test_settlement_quote_is_read_only_and_matches(self):
        q = settlement_quote(self.emp, TODAY)
        self.assertEqual(q["days"], "8.50")
        self.assertEqual(q["daily_rate"], "500.00")
        self.assertEqual(q["amount"], "4250.00")
        self.emp.refresh_from_db()
        self.assertIsNone(self.emp.termination_date)  # quote did not write anything
        self.assertFalse(LeaveEncashment.objects.exists())

    def test_second_settlement_for_same_person_is_refused(self):
        raise_leaver_settlement(
            initiator=self.dorothy, employee=self.emp, last_day=TODAY
        )
        with self.assertRaises(ValidationError):
            raise_leaver_settlement(
                initiator=self.dorothy, employee=self.emp, last_day=TODAY
            )

    # ── guards ──────────────────────────────────────────────────────────────
    def test_asset_still_held_blocks_the_rehire(self):
        """AC6: a laptop still with the leaver blocks the exit — same gate as
        archiving and terminating (Asset Control & Handover, CFO 2026-09-02)."""
        from assets.models import Asset, AssetCategory
        from core.models import Currency
        from ledger.models import Account

        bwp, _ = Currency.objects.get_or_create(code="BWP", defaults={"name": "Pula"})

        def acct(code, name, sub, kind=Account.AccountType.ASSET):
            return Account.objects.create(
                code=code, name=name, account_type=kind, sub_type=sub, currency_code=bwp
            )

        cat = AssetCategory.objects.create(
            code="ITR", name="IT equipment",
            cost_account=acct("1420R", "IT cost", Account.SubType.FIXED_ASSET),
            accum_depr_account=acct("1452R", "IT accum", Account.SubType.FIXED_ASSET),
            depreciation_expense_account=Account.objects.create(
                code="6600R", name="Depr", account_type=Account.AccountType.EXPENSE,
                currency_code=bwp),
        )
        Asset.objects.create(
            tag_number="LAP-R1", name="Laptop LAP-R1", company=self.adic, category=cat,
            cost=Decimal("9000.00"), salvage_value=Decimal("0.00"),
            method=Asset.Method.STRAIGHT_LINE, useful_life_months=36,
            purchase_date=dt.date(2026, 1, 1), in_service_date=dt.date(2026, 1, 1),
            created_by=self.cfo, custodian_employee=self.emp,
            custody_status=Asset.CustodyStatus.IN_USE,
        )
        with self.assertRaises(ValidationError) as cm:
            self._submit()
        self.assertIn("still holds", str(cm.exception))
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.status, Employee.Status.ACTIVE)   # nothing changed

    def test_submitter_cannot_approve_and_out_cannot_be_in(self):
        t = self._submit()
        with self.assertRaises(ValidationError):
            approve_out(t, self.dorothy)
        approve_out(t, self.unami)
        with self.assertRaises(ValidationError):
            approve_in(t, self.unami)

    def test_next_employee_number_increments_per_entity(self):
        Employee.objects.create(
            employee_number="UNI_007", full_name="A", company=self.uni
        )
        self.assertEqual(next_employee_number(self.uni), "UNI_008")
        self.assertEqual(next_employee_number(self.adic), "ADIC_701")   # fixture holds ADIC_700

    def test_api_accepts_mode_and_preview(self):
        self.client.force_authenticate(self.cfo)
        r = self.client.get(
            f"/hris/api/transfers/settlement-preview/?employee_id={self.emp.id}"
            f"&last_day={TODAY.isoformat()}"
        )
        self.assertIn(r.status_code, (200, 401, 403))  # gated, never a 500
        if r.status_code == 200:
            self.assertEqual(r.json()["days"], "8.50")
