"""
The quarterly Board/EXCO pack and the AML/CFT registers (CFO 2026-09-09).

Separate file from test_weekly_objectives.py because these test the COMPLIANCE
side — the registers a Botswana insurer's AML/CFT Officer must keep — rather
than the weekly engine itself.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as tz

from core.models import OmniTask
from hris.models import HRISProfile
from hris.weekly_objective_models import Cadence, Direction, WeeklyObjective
from payroll.models import Employee

CMD = 'weekly_objectives_cycle'
READ = 'hris.objective_counters.read'

Q3_START = dt.date(2026, 7, 1)      # the quarter being reported on
Q3_DUE = dt.date(2026, 10, 5)       # the 5th of the month after it ends


class QuarterMathTests(TestCase):
    """The CFO's rule: the pack reaches EXCO by the 5th of every quarter ending."""

    def test_the_quarter_just_ended_is_the_one_reported_on(self):
        from hris.management.commands.weekly_objectives_cycle import quarter_start_for
        # Raised on the first day of Q4 — it asks about Q3, not the quarter
        # that has not happened yet.
        self.assertEqual(quarter_start_for(dt.date(2026, 10, 1)), Q3_START)

    def test_a_mid_quarter_run_still_reports_the_previous_quarter(self):
        from hris.management.commands.weekly_objectives_cycle import quarter_start_for
        self.assertEqual(quarter_start_for(dt.date(2026, 11, 20)), Q3_START)

    def test_january_rolls_back_a_year(self):
        from hris.management.commands.weekly_objectives_cycle import quarter_start_for
        self.assertEqual(quarter_start_for(dt.date(2027, 1, 2)), dt.date(2026, 10, 1))

    def test_all_four_quarters_land_on_a_fifth(self):
        from iso_compliance.aml_models import board_pack_due
        self.assertEqual(board_pack_due(2026, 1), dt.date(2026, 4, 5))
        self.assertEqual(board_pack_due(2026, 2), dt.date(2026, 7, 5))
        self.assertEqual(board_pack_due(2026, 3), dt.date(2026, 10, 5))
        # Q4 must cross the year end without landing in the wrong January.
        self.assertEqual(board_pack_due(2026, 4), dt.date(2027, 1, 5))

    def test_quarter_ends_are_the_real_month_ends(self):
        from iso_compliance.aml_models import quarter_end
        self.assertEqual(quarter_end(2026, 1), dt.date(2026, 3, 31))
        self.assertEqual(quarter_end(2026, 2), dt.date(2026, 6, 30))
        self.assertEqual(quarter_end(2026, 3), dt.date(2026, 9, 30))
        self.assertEqual(quarter_end(2026, 4), dt.date(2026, 12, 31))


class QuarterlyTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'kb.test', email='kb.test@alphadirect.co.bw', password='x')
        employee = Employee.objects.create(
            full_name='Compliance Tester', email='kb.test@alphadirect.co.bw')
        self.profile = HRISProfile.objects.create(employee=employee)
        WeeklyObjective.objects.create(
            profile=self.profile, key='board-pack',
            title='Quarterly compliance report to EXCO',
            counter='omni_board_pack_outstanding',
            cadence=Cadence.QUARTERLY, direction=Direction.NIL, target=0)

    def test_due_the_fifth_and_claims_no_week(self):
        with mock.patch(READ, return_value=1):
            call_command(CMD, '--raise', '--commit', '--cadence=quarterly',
                         f'--period={Q3_START}')
        task = OmniTask.objects.get(assignee=self.user)
        self.assertEqual(task.due_at, Q3_DUE)
        self.assertEqual(task.due_time, dt.time(16, 0))
        # A quarterly task stamped with a Monday would show up in that week's
        # plan and read as something owed by Friday.
        self.assertIsNone(task.week_of)

    def test_filing_the_pack_closes_the_task(self):
        with mock.patch(READ, return_value=1):          # outstanding at raise
            call_command(CMD, '--raise', '--commit', '--cadence=quarterly',
                         f'--period={Q3_START}')
        with mock.patch(READ, return_value=0):          # filed by the deadline
            call_command(CMD, '--settle', '--commit', '--cadence=quarterly',
                         f'--period={Q3_START}')
        self.assertEqual(OmniTask.objects.get().status, OmniTask.Status.DONE)

    def test_not_filing_the_pack_leaves_it_open(self):
        with mock.patch(READ, return_value=1):
            call_command(CMD, '--raise', '--commit', '--cadence=quarterly',
                         f'--period={Q3_START}')
            call_command(CMD, '--settle', '--commit', '--cadence=quarterly',
                         f'--period={Q3_START}')
        self.assertEqual(OmniTask.objects.get().status, OmniTask.Status.PENDING)

    def test_a_weekly_run_does_not_raise_the_quarterly_task(self):
        with mock.patch(READ, return_value=1):
            call_command(CMD, '--raise', '--commit', '--cadence=weekly',
                         '--period=2026-09-14')
        self.assertEqual(OmniTask.objects.count(), 0)


class BoardPackDischargeTests(TestCase):
    """A pack only counts when it exists AND actually went out."""

    def _report(self, **kw):
        from iso_compliance.aml_models import ComplianceReport
        defaults = dict(period_year=2026, period_quarter=2)
        defaults.update(kw)
        return ComplianceReport.objects.create(**defaults)

    def test_a_draft_with_no_document_is_not_a_report(self):
        from iso_compliance.aml_models import ComplianceReport
        self.assertFalse(self._report(status=ComplianceReport.Status.DRAFT).is_discharged)

    def test_marking_it_sent_with_nothing_attached_is_not_a_report(self):
        """The exact self-declared completion this module exists to stop."""
        from iso_compliance.aml_models import ComplianceReport
        self.assertFalse(self._report(status=ComplianceReport.Status.SENT).is_discharged)

    def test_sent_with_a_document_is_discharged(self):
        from iso_compliance.aml_models import ComplianceReport
        r = self._report(status=ComplianceReport.Status.SENT,
                         document='compliance/board-packs/q2.pdf')
        self.assertTrue(r.is_discharged)

    def test_the_counter_reads_the_quarter_that_just_ended(self):
        from hris.objective_counters import omni_board_pack_outstanding
        from iso_compliance.aml_models import ComplianceReport, quarter_of

        today = tz.localdate()
        this_q_first = dt.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        year, quarter = quarter_of(this_q_first - dt.timedelta(days=1))

        self.assertEqual(omni_board_pack_outstanding(), 1)      # nothing filed
        ComplianceReport.objects.create(
            period_year=year, period_quarter=quarter,
            status=ComplianceReport.Status.SENT,
            document='compliance/board-packs/x.pdf')
        self.assertEqual(omni_board_pack_outstanding(), 0)

    def test_filing_the_wrong_quarter_does_not_discharge_this_one(self):
        from hris.objective_counters import omni_board_pack_outstanding
        from iso_compliance.aml_models import ComplianceReport

        # A pack filed for a quarter that is not the one just ended must leave
        # the obligation open — otherwise last year's pack closes this year's.
        ComplianceReport.objects.create(
            period_year=2020, period_quarter=1,
            status=ComplianceReport.Status.SENT,
            document='compliance/board-packs/ancient.pdf')
        self.assertEqual(omni_board_pack_outstanding(), 1)


class RegisterCounterTests(TestCase):
    """Each register counter must count the right thing, and only that."""

    def test_a_confirmed_match_not_reported_to_the_fia_is_counted(self):
        from hris.objective_counters import omni_sanctions_match_unreported
        from iso_compliance.aml_models import SanctionsScreening

        self.assertEqual(omni_sanctions_match_unreported(), 0)
        m = SanctionsScreening.objects.create(
            subject_name='Flagged Party', result=SanctionsScreening.Result.MATCH)
        self.assertEqual(omni_sanctions_match_unreported(), 1)
        self.assertTrue(m.needs_fia_report)
        m.reported_to_fia_at = tz.now()
        m.save()
        self.assertEqual(omni_sanctions_match_unreported(), 0)
        self.assertFalse(m.needs_fia_report)

    def test_a_clear_screening_is_never_an_unreported_match(self):
        from hris.objective_counters import omni_sanctions_match_unreported
        from iso_compliance.aml_models import SanctionsScreening

        SanctionsScreening.objects.create(
            subject_name='Clean Party', result=SanctionsScreening.Result.CLEAR)
        self.assertEqual(omni_sanctions_match_unreported(), 0)

    def test_pep_unassessed_counts_only_the_unanswered(self):
        from hris.objective_counters import omni_pep_unassessed
        from iso_compliance.aml_models import SanctionsScreening

        SanctionsScreening.objects.create(subject_name='A')          # default unchecked
        SanctionsScreening.objects.create(
            subject_name='B', pep_status=SanctionsScreening.PEP.NONE)
        self.assertEqual(omni_pep_unassessed(), 1)

    def test_a_risk_without_an_owner_or_a_plan_does_not_count(self):
        """The register must not be fillable with headings."""
        from hris.objective_counters import omni_risk_register_complete
        from iso_compliance.models import Risk

        Risk.objects.create(ref='R-001', title='Heading only')
        self.assertEqual(omni_risk_register_complete(), 0)
        Risk.objects.create(ref='R-002', title='Real risk', owner='Kakale Botana',
                            treatment_plan='Screen every new counterparty.')
        self.assertEqual(omni_risk_register_complete(), 1)

    def test_a_closed_risk_is_not_an_open_one(self):
        from hris.objective_counters import omni_risk_register_complete
        from iso_compliance.models import Risk

        Risk.objects.create(ref='R-003', title='Done', owner='K', treatment_plan='p',
                            status=Risk.STATUS_CLOSED)
        self.assertEqual(omni_risk_register_complete(), 0)

    def test_complaints_count_only_once_past_the_service_standard(self):
        from hris.objective_counters import omni_complaints_overdue
        from iso_compliance.aml_models import COMPLAINT_SLA_DAYS, CustomerComplaint

        today = tz.localdate()
        CustomerComplaint.objects.create(
            received_on=today - dt.timedelta(days=COMPLAINT_SLA_DAYS - 1),
            complainant='Recent', summary='x')
        self.assertEqual(omni_complaints_overdue(), 0)
        CustomerComplaint.objects.create(
            received_on=today - dt.timedelta(days=COMPLAINT_SLA_DAYS + 1),
            complainant='Stale', summary='x')
        self.assertEqual(omni_complaints_overdue(), 1)

    def test_a_resolved_complaint_is_never_overdue(self):
        from hris.objective_counters import omni_complaints_overdue
        from iso_compliance.aml_models import CustomerComplaint

        CustomerComplaint.objects.create(
            received_on=tz.localdate() - dt.timedelta(days=400),
            complainant='Old but done', summary='x',
            status=CustomerComplaint.Status.RESOLVED)
        self.assertEqual(omni_complaints_overdue(), 0)

    def test_training_counts_people_not_records(self):
        # The register is scoped to Alpha Direct as of 2026-09-16, so these need
        # a company. Without one they look exactly like the 49 phantom rows the
        # scoping exists to drop, and correctly no longer count.
        from core.models import Company
        from hris.objective_counters import omni_aml_training_outstanding
        from iso_compliance.aml_models import AMLTrainingRecord

        adic = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance'})[0]
        a = Employee.objects.create(full_name='Trained Twice', email='a@alphadirect.co.bw',
                                    employee_number='T-0001', company=adic)
        Employee.objects.create(full_name='Never Trained', email='b@alphadirect.co.bw',
                                employee_number='T-0002', company=adic)
        self.assertEqual(omni_aml_training_outstanding(), 2)
        for _ in range(2):
            AMLTrainingRecord.objects.create(employee=a, completed_on=tz.localdate())
        self.assertEqual(omni_aml_training_outstanding(), 1)

    def test_training_older_than_a_year_no_longer_counts(self):
        from core.models import Company
        from hris.objective_counters import omni_aml_training_outstanding
        from iso_compliance.aml_models import TRAINING_VALID_DAYS, AMLTrainingRecord
        adic = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance'})[0]
        e = Employee.objects.create(full_name='Lapsed', email='c@alphadirect.co.bw',
                                    employee_number='T-0003', company=adic)
        AMLTrainingRecord.objects.create(
            employee=e,
            completed_on=tz.localdate() - dt.timedelta(days=TRAINING_VALID_DAYS + 1))
        self.assertEqual(omni_aml_training_outstanding(), 1)

    def test_an_str_stays_counted_until_it_is_filed(self):
        from hris.objective_counters import omni_str_unfiled
        from iso_compliance.aml_models import SuspiciousTransactionReport

        r = SuspiciousTransactionReport.objects.create(
            subject_name='Odd payer', detected_on=tz.localdate(), description='x')
        self.assertEqual(omni_str_unfiled(), 1)
        r.status = SuspiciousTransactionReport.Status.FILED
        r.save()
        self.assertEqual(omni_str_unfiled(), 0)

    def test_regulatory_breaches_count_only_while_open(self):
        from hris.objective_counters import omni_regulatory_breach_open
        from iso_compliance.aml_models import RegulatoryBreach

        b = RegulatoryBreach.objects.create(
            title='Late return', discovered_on=tz.localdate())
        self.assertEqual(omni_regulatory_breach_open(), 1)
        b.status = RegulatoryBreach.Status.CLOSED
        b.save()
        self.assertEqual(omni_regulatory_breach_open(), 0)

    def test_a_data_breach_is_not_a_regulatory_breach(self):
        """core.BreachIncident belongs to the DPO on the IDPC clock. Mixing the
        two would put a leaked customer list on the AML officer's number."""
        from core.models import BreachIncident
        from hris.objective_counters import omni_regulatory_breach_open

        BreachIncident.objects.create(title='Leaked list', discovered_at=tz.now())
        self.assertEqual(omni_regulatory_breach_open(), 0)

    def test_every_registered_counter_name_resolves(self):
        """A typo in a seeded objective must fail here, not silently on a Sunday."""
        from hris.objective_counters import COUNTERS

        self.assertIn('omni_board_pack_outstanding', COUNTERS)
        for name, fn in COUNTERS.items():
            self.assertTrue(callable(fn), name)


class SeedTests(TestCase):
    """The seed must match reality — a bad counter name would fail on a Sunday."""

    def test_every_seeded_counter_is_registered(self):
        from hris.management.commands.seed_manager_objectives import OBJECTIVES
        from hris.objective_counters import COUNTERS

        for email, specs in OBJECTIVES.items():
            for spec in specs:
                self.assertIn(spec['counter'], COUNTERS,
                              f'{email} / {spec["key"]}')

    def test_every_seeded_direction_and_cadence_is_valid(self):
        from hris.management.commands.seed_manager_objectives import OBJECTIVES

        directions = {d for d, _ in Direction.choices}
        cadences = {c for c, _ in Cadence.choices}
        for specs in OBJECTIVES.values():
            for spec in specs:
                self.assertIn(spec['direction'], directions, spec['key'])
                self.assertIn(spec.get('cadence', 'weekly'), cadences, spec['key'])

    def test_seeded_keys_are_unique_per_manager(self):
        from hris.management.commands.seed_manager_objectives import OBJECTIVES

        for email, specs in OBJECTIVES.items():
            keys = [s['key'] for s in specs]
            self.assertEqual(len(keys), len(set(keys)), email)

    def test_a_reduce_objective_always_carries_a_real_target(self):
        """A REDUCE with target 0 would close itself on day one and prove nothing."""
        from hris.management.commands.seed_manager_objectives import OBJECTIVES

        for specs in OBJECTIVES.values():
            for spec in specs:
                if spec['direction'] == Direction.REDUCE:
                    self.assertGreater(spec['target'], 0, spec['key'])

    def test_seeding_is_idempotent(self):
        from hris.management.commands.seed_manager_objectives import OBJECTIVES

        email = 'kbotana@alphadirect.co.bw'
        employee = Employee.objects.create(
            full_name='Kakale Test', email=email, employee_number='S-0001')
        HRISProfile.objects.create(employee=employee)
        expected = len(OBJECTIVES[email])

        call_command('seed_manager_objectives', '--commit', f'--email={email}')
        self.assertEqual(WeeklyObjective.objects.count(), expected)
        call_command('seed_manager_objectives', '--commit', f'--email={email}')
        self.assertEqual(WeeklyObjective.objects.count(), expected)

    def test_dry_run_seeds_nothing(self):
        employee = Employee.objects.create(
            full_name='Kakale Test', email='kbotana@alphadirect.co.bw',
            employee_number='S-0002')
        HRISProfile.objects.create(employee=employee)
        call_command('seed_manager_objectives', '--email=kbotana@alphadirect.co.bw')
        self.assertEqual(WeeklyObjective.objects.count(), 0)
