"""
The Data Protection Officer's objectives (CFO 2026-09-09, "do oratile next").

No new models were added for these — every register already existed in Omni.
What was missing was anything that counted them, which is how ten tasks could be
ticked "done" in July with nothing to check them against.

The test that matters most here is the LAST class: two officers now owe a
quarterly report, and the AML pack must never discharge the DPO's.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone as tz

from core.models import BreachIncident, DataSubjectRequest
from iso_compliance.aml_models import ComplianceReport, quarter_of
from iso_compliance.models import (
    DPIA, InternalPolicy, RopaEntry, SOPAcknowledgement, SOPDocument, VendorRegister,
)


def _this_quarter_just_ended():
    today = tz.localdate()
    first = dt.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    return quarter_of(first - dt.timedelta(days=1))


class RopaCounterTests(TestCase):
    def test_an_entry_needs_both_a_confirmation_and_a_lawful_basis(self):
        from hris.objective_counters import omni_ropa_confirmed

        RopaEntry.objects.create(source_table='policies', source_field='omang')
        self.assertEqual(omni_ropa_confirmed(), 0)

        # Confirmed but with no lawful basis — someone clicked past it.
        RopaEntry.objects.create(source_table='policies', source_field='dob',
                                 confirmed_at=tz.now())
        self.assertEqual(omni_ropa_confirmed(), 0)

        # A basis typed in but never confirmed.
        RopaEntry.objects.create(source_table='claims', source_field='bank',
                                 confirmed_lawful_basis='contract')
        self.assertEqual(omni_ropa_confirmed(), 0)

        RopaEntry.objects.create(source_table='claims', source_field='email',
                                 confirmed_at=tz.now(),
                                 confirmed_lawful_basis='legitimate_interest')
        self.assertEqual(omni_ropa_confirmed(), 1)


class PolicyCounterTests(TestCase):
    def test_a_placeholder_is_not_a_policy(self):
        from hris.objective_counters import omni_policies_real

        InternalPolicy.objects.create(policy_name='Privacy notice',
                                      is_placeholder=True)
        self.assertEqual(omni_policies_real(), 0)
        InternalPolicy.objects.create(policy_name='Retention policy',
                                      is_placeholder=False)
        self.assertEqual(omni_policies_real(), 1)

    def test_a_known_gap_does_not_count_as_a_real_policy(self):
        from hris.objective_counters import omni_policies_real

        InternalPolicy.objects.create(policy_name='Missing one',
                                      is_placeholder=False, is_gap=True)
        self.assertEqual(omni_policies_real(), 0)


class VendorCounterTests(TestCase):
    def test_a_vendor_without_the_signed_agreement_does_not_count(self):
        from hris.objective_counters import omni_vendor_dpa_signed

        VendorRegister.objects.create(vendor_name='Listed only',
                                      dpa_contract_signed=False)
        self.assertEqual(omni_vendor_dpa_signed(), 0)
        VendorRegister.objects.create(vendor_name='Properly bound',
                                      dpa_contract_signed=True)
        self.assertEqual(omni_vendor_dpa_signed(), 1)


class DpiaCounterTests(TestCase):
    def test_signoff_is_the_three_signatures_not_the_status_field(self):
        """A status nudged to 'approved' by hand is not sign-off."""
        from hris.objective_counters import omni_dpia_unsigned

        d = DPIA.objects.create(project='Claims AI', status=DPIA.STATUS_APPROVED)
        self.assertEqual(omni_dpia_unsigned(), 1)

        d.dpo_signed = True
        d.compliance_signed = True
        d.save()
        self.assertEqual(omni_dpia_unsigned(), 1)      # CFO still missing

        d.cfo_signed = True
        d.save()
        self.assertEqual(omni_dpia_unsigned(), 0)

    def test_a_closed_assessment_is_finished_business(self):
        from hris.objective_counters import omni_dpia_unsigned

        DPIA.objects.create(project='Retired project', status=DPIA.STATUS_CLOSED)
        self.assertEqual(omni_dpia_unsigned(), 0)


class DsrCounterTests(TestCase):
    def _dsr(self, **kw):
        defaults = dict(subject_name='A Person', received_at=tz.now(),
                        due_date=tz.localdate())
        defaults.update(kw)
        return DataSubjectRequest.objects.create(**defaults)

    def test_only_requests_past_the_clock_count(self):
        from hris.objective_counters import omni_dsr_overdue

        self._dsr(due_date=tz.localdate() + dt.timedelta(days=5))
        self.assertEqual(omni_dsr_overdue(), 0)
        self._dsr(due_date=tz.localdate() - dt.timedelta(days=1))
        self.assertEqual(omni_dsr_overdue(), 1)

    def test_a_completed_request_is_never_overdue(self):
        from hris.objective_counters import omni_dsr_overdue

        self._dsr(due_date=tz.localdate() - dt.timedelta(days=40),
                  status=DataSubjectRequest.Status.COMPLETED)
        self.assertEqual(omni_dsr_overdue(), 0)

    def test_a_rejected_request_is_never_overdue(self):
        from hris.objective_counters import omni_dsr_overdue

        self._dsr(due_date=tz.localdate() - dt.timedelta(days=40),
                  status=DataSubjectRequest.Status.REJECTED)
        self.assertEqual(omni_dsr_overdue(), 0)


class BreachCounterTests(TestCase):
    def test_a_breach_inside_72_hours_is_not_yet_a_failure(self):
        from hris.objective_counters import omni_breach_unnotified

        BreachIncident.objects.create(title='Just found', discovered_at=tz.now(),
                                      reportable=True, idpc_notified=False)
        self.assertEqual(omni_breach_unnotified(), 0)

    def test_past_72_hours_without_notifying_the_regulator_counts(self):
        from hris.objective_counters import omni_breach_unnotified

        BreachIncident.objects.create(
            title='Sat on it', discovered_at=tz.now() - dt.timedelta(hours=80),
            reportable=True, idpc_notified=False)
        self.assertEqual(omni_breach_unnotified(), 1)

    def test_notifying_the_regulator_clears_it(self):
        from hris.objective_counters import omni_breach_unnotified

        b = BreachIncident.objects.create(
            title='Handled', discovered_at=tz.now() - dt.timedelta(hours=80),
            reportable=True, idpc_notified=False)
        self.assertEqual(omni_breach_unnotified(), 1)
        b.idpc_notified = True
        b.save()
        self.assertEqual(omni_breach_unnotified(), 0)

    def test_a_non_reportable_breach_is_not_counted(self):
        from hris.objective_counters import omni_breach_unnotified

        BreachIncident.objects.create(
            title='Internal only', discovered_at=tz.now() - dt.timedelta(hours=80),
            reportable=False, idpc_notified=False)
        self.assertEqual(omni_breach_unnotified(), 0)


class SopAckCounterTests(TestCase):
    def test_it_counts_people_reached_not_signatures_collected(self):
        """500 signatures from ten keen people is not company-wide coverage."""
        from hris.objective_counters import omni_sop_ack_staff

        sops = [SOPDocument.objects.create(title=f'SOP {i}', sop_number=f'S-{i}')
                for i in range(3)]
        keen = User.objects.create_user('keen', email='keen@alphadirect.co.bw')
        for s in sops:
            SOPAcknowledgement.objects.create(sop=s, user=keen)
        self.assertEqual(omni_sop_ack_staff(), 1)

        other = User.objects.create_user('other', email='other@alphadirect.co.bw')
        SOPAcknowledgement.objects.create(sop=sops[0], user=other)
        self.assertEqual(omni_sop_ack_staff(), 2)


class TwoOfficersOneQuarterTests(TestCase):
    """The collision that would have silently discharged the DPO's duty."""

    def _file(self, kind):
        year, quarter = _this_quarter_just_ended()
        return ComplianceReport.objects.create(
            kind=kind, period_year=year, period_quarter=quarter,
            status=ComplianceReport.Status.SENT,
            document='compliance/board-packs/x.pdf')

    def test_both_packs_start_outstanding(self):
        from hris.objective_counters import (
            omni_board_pack_outstanding, omni_dpo_pack_outstanding)
        self.assertEqual(omni_board_pack_outstanding(), 1)
        self.assertEqual(omni_dpo_pack_outstanding(), 1)

    def test_the_aml_pack_does_not_discharge_the_dpo_duty(self):
        from hris.objective_counters import (
            omni_board_pack_outstanding, omni_dpo_pack_outstanding)

        self._file(ComplianceReport.Kind.AML)
        self.assertEqual(omni_board_pack_outstanding(), 0)
        self.assertEqual(omni_dpo_pack_outstanding(), 1)   # still owed

    def test_the_dpo_pack_does_not_discharge_the_aml_duty(self):
        from hris.objective_counters import (
            omni_board_pack_outstanding, omni_dpo_pack_outstanding)

        self._file(ComplianceReport.Kind.DATA_PROT)
        self.assertEqual(omni_dpo_pack_outstanding(), 0)
        self.assertEqual(omni_board_pack_outstanding(), 1)

    def test_both_officers_can_file_for_the_same_quarter(self):
        """Before `kind` existed the unique constraint made this impossible."""
        from hris.objective_counters import (
            omni_board_pack_outstanding, omni_dpo_pack_outstanding)

        self._file(ComplianceReport.Kind.AML)
        self._file(ComplianceReport.Kind.DATA_PROT)
        self.assertEqual(omni_board_pack_outstanding(), 0)
        self.assertEqual(omni_dpo_pack_outstanding(), 0)

    def test_a_dpo_pack_sent_with_no_document_still_leaves_it_owed(self):
        from hris.objective_counters import omni_dpo_pack_outstanding

        year, quarter = _this_quarter_just_ended()
        ComplianceReport.objects.create(
            kind=ComplianceReport.Kind.DATA_PROT, period_year=year,
            period_quarter=quarter, status=ComplianceReport.Status.SENT)
        self.assertEqual(omni_dpo_pack_outstanding(), 1)


class OratileSeedTests(TestCase):
    EMAIL = 'otlhomelang@alphadirect.co.bw'

    def test_she_is_seeded_and_every_counter_resolves(self):
        from hris.management.commands.seed_manager_objectives import OBJECTIVES
        from hris.objective_counters import COUNTERS

        self.assertIn(self.EMAIL, OBJECTIVES)
        for spec in OBJECTIVES[self.EMAIL]:
            self.assertIn(spec['counter'], COUNTERS, spec['key'])

    def test_ropa_is_hers_and_not_kakales(self):
        """CFO 2026-09-09: ROPA is data protection, not AML."""
        from hris.management.commands.seed_manager_objectives import OBJECTIVES

        hers = {s['key'] for s in OBJECTIVES[self.EMAIL]}
        kakale = {s['key'] for s in OBJECTIVES['kbotana@alphadirect.co.bw']}
        self.assertIn('ropa-confirmed', hers)
        self.assertNotIn('ropa-confirmed', kakale)
        self.assertFalse(hers & kakale, 'the two officers must not share an objective key')

    def test_the_two_quarterly_packs_use_different_counters(self):
        from hris.management.commands.seed_manager_objectives import OBJECTIVES

        def pack(email):
            return next(s for s in OBJECTIVES[email] if s.get('cadence') == 'quarterly')

        self.assertNotEqual(pack(self.EMAIL)['counter'],
                            pack('kbotana@alphadirect.co.bw')['counter'])
