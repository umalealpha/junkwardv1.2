"""CFO 2026-09-05: Dorothy decides roster flags alongside Unami; HR (HRIS tier)
may confirm a Time Doctor link; the emailed one-click feedback page carries a
"not my report" action; the recipient access check refuses a person who cannot
sign in."""

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from core.models import Company, Role, UserRoleAssignment
from hris.models import HRISProfile
from hris.roster_flag_models import (
    FlagKind,
    FlagStatus,
    RosterFlag,
    can_decide,
    decider_user,
)
from payroll.models import Employee


class DorothyDecidesTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unami = User.objects.create_user(
            "ubutale", email="ubutale@alphadirect.co.bw"
        )
        cls.dorothy = User.objects.create_user(
            "dikgopoleng", email="dikgopoleng@alphadirect.co.bw"
        )
        cls.thapelo = User.objects.create_user(
            "tmorapedi", email="tmorapedi@alphadirect.co.bw"
        )

    def test_unami_and_dorothy_decide_thapelo_does_not(self):
        self.assertTrue(can_decide(self.unami))
        self.assertTrue(can_decide(self.dorothy))
        self.assertFalse(can_decide(self.thapelo))

    def test_decider_falls_back_to_dorothy_when_unami_is_inactive(self):
        self.unami.is_active = False
        self.unami.save(update_fields=["is_active"])
        self.assertEqual(decider_user().pk, self.dorothy.pk)


class HrCanLinkTimeDoctorTest(APITestCase):
    def test_hris_role_may_confirm_link_but_not_flip_switch(self):
        from hris.workforce_views import _can_link, _can_toggle

        role, _ = Role.objects.get_or_create(
            code="HRIS", defaults={"name": "HRIS", "level": 3}
        )
        dorothy = User.objects.create_user(
            "dikgopoleng", email="dikgopoleng@alphadirect.co.bw"
        )
        UserRoleAssignment.objects.create(user=dorothy, role=role)
        plain = User.objects.create_user("plain", email="plain@alphadirect.co.bw")
        self.assertTrue(_can_link(dorothy))
        self.assertFalse(_can_toggle(dorothy))
        self.assertFalse(_can_link(plain))


class OneClickNotMineTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code="OC", name="One Click Co")
        cls.mgr_user = User.objects.create_user(
            "bharath", email="bbalasubramanian@alphadirect.co.bw"
        )
        cls.mgr = Employee.objects.create(
            employee_number="OC-M",
            full_name="Bharath B",
            company=cls.co,
            user=cls.mgr_user,
            email="bbalasubramanian@alphadirect.co.bw",
        )
        cls.report = Employee.objects.create(
            employee_number="OC-1", full_name="Katlego G", company=cls.co
        )
        cls.report_profile = HRISProfile.objects.create(
            employee=cls.report, manager=cls.mgr
        )
        User.objects.create_user("ubutale", email="ubutale@alphadirect.co.bw")
        User.objects.create_user("dikgopoleng", email="dikgopoleng@alphadirect.co.bw")

    def test_not_mine_link_raises_a_flag_and_keeps_the_person(self):
        from django.test import override_settings
        from hris.manager_feedback_actions import make_token

        token = make_token(self.mgr, 2026, 8)
        with override_settings(ELRA_PERF_ENABLED=True):
            r = self.client.get(f"/hris/api/manager-feedback/{token}/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Not my report", r.content.decode())
            r = self.client.post(
                f"/hris/api/manager-feedback/{token}/not-mine/",
                {"profile": str(self.report_profile.pk), "note": "Moved to Unicoin"},
            )
            self.assertEqual(r.status_code, 200, r.content[:300])
        flag = RosterFlag.objects.get(profile=self.report_profile)
        self.assertEqual(flag.kind, FlagKind.NOT_MY_REPORT)
        self.assertEqual(flag.status, FlagStatus.OPEN)
        self.assertEqual(flag.raised_by_id, self.mgr_user.pk)
        # Still on the roster — a flag never removes anyone.
        self.report_profile.refresh_from_db()
        self.assertEqual(self.report_profile.manager_id, self.mgr.id)
        # Both HR deciders were told.
        from core.models import OmniTask

        self.assertEqual(
            OmniTask.objects.filter(source__startswith="rflag:").count(), 2
        )


class RecipientAccessCheckTest(APITestCase):
    def test_no_login_is_refused_active_login_passes(self):
        from core.access_check import can_reach, omni_paths_in

        co = Company.objects.create(code="AC", name="Access Co")
        Employee.objects.create(
            employee_number="AC-1",
            full_name="No Login Person",
            email="nologin@alphadirect.co.bw",
            company=co,
        )
        User.objects.create_user(
            "kmolefe", email="kmolefe@alphadirect.co.bw", password="x"
        )
        self.assertFalse(can_reach("nologin@alphadirect.co.bw", "/sop-bank").ok)
        self.assertFalse(can_reach("stranger@alphadirect.co.bw", "/sop-bank").ok)
        self.assertTrue(can_reach("kmolefe@alphadirect.co.bw", "/sop-bank").ok)
        self.assertFalse(can_reach("kmolefe@alphadirect.co.bw", "/hris/transfers").ok)
        self.assertEqual(
            omni_paths_in("open https://omni.alphadirect.co.bw/sop-bank?x=1 now"),
            ["/sop-bank"],
        )


class AccessCheckPureTest(SimpleTestCase):
    def test_paths_are_deduplicated_in_order(self):
        from core.access_check import omni_paths_in

        text = (
            "https://omni.alphadirect.co.bw/hris/leave and "
            "https://omni.alphadirect.co.bw/sop-bank then https://omni.alphadirect.co.bw/hris/leave#x"
        )
        self.assertEqual(omni_paths_in(text), ["/hris/leave", "/sop-bank"])
