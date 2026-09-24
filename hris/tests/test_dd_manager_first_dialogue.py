"""Development Dialogue retest, 21-Sep-2026 — the three findings that stayed open
after bd62814c fixed the crash and the sign/save race.

  P1  a manager whose report has NO dialogue yet was answered 403, and a save
      that added that report returned success WITHOUT creating anything
  P2  a dialogue with no competency structure opens as an empty review — there
      is nothing for the manager to score

Each test is written to fail against the code as it stood before this change.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from hris.models import DevelopmentDialogue, HRISProfile
from payroll.models import Employee


class ManagerFirstDialogue(TestCase):
    """A manager with reports but no dialogue rows yet."""

    @classmethod
    def setUpTestData(cls):
        cls.boss_emp = Employee.objects.create(
            employee_number='DD-BOSS', full_name='Kabo Manager',
            email='kabo@example.com', status='active')
        cls.report_emp = Employee.objects.create(
            employee_number='DD-REP', full_name='Neo Report',
            email='neo@example.com', status='active')
        cls.other_emp = Employee.objects.create(
            employee_number='DD-OTH', full_name='Tumi Stranger',
            email='tumi@example.com', status='active')
        HRISProfile.objects.create(employee=cls.report_emp, manager=cls.boss_emp)
        HRISProfile.objects.create(employee=cls.other_emp)
        cls.boss = User.objects.create_user('kabo', email='kabo@example.com')

    def setUp(self):
        self.c = APIClient()
        self.c.force_authenticate(self.boss)

    def test_manager_can_open_cockpit_with_no_dialogues_on_file(self):
        """P1a: zero dialogue rows must not read as 'no access'."""
        self.assertFalse(DevelopmentDialogue.objects.exists())
        r = self.c.get('/hris/api/talent/cockpit/')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data['scope'], 'team')
        self.assertEqual(r.data['people'], [])

    def test_manager_save_creates_the_first_dialogue_for_a_report(self):
        """P1b: the save reported success and wrote nothing."""
        ref = 'neo@example.com::2026'
        r = self.c.put('/hris/api/talent/cockpit/', {'people': [
            {'id': ref, 'name': 'Neo Report', 'period': '2026',
             'email': 'neo@example.com'},
        ]}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data['saved'], 1)
        row = DevelopmentDialogue.objects.filter(ref=ref).first()
        self.assertIsNotNone(row, 'the first dialogue was never created')
        self.assertEqual(row.email, 'neo@example.com')
        self.assertEqual(row.employee_id, self.report_emp.id)

    def test_manager_cannot_create_for_someone_who_is_not_their_report(self):
        """The new create path must not become a way in to the whole company."""
        ref = 'tumi@example.com::2026'
        r = self.c.put('/hris/api/talent/cockpit/', {'people': [
            {'id': ref, 'name': 'Tumi Stranger', 'period': '2026',
             'email': 'tumi@example.com'},
        ]}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data['saved'], 0)
        self.assertFalse(DevelopmentDialogue.objects.filter(ref=ref).exists())

    def test_manager_cannot_hijack_an_existing_out_of_scope_dialogue(self):
        """Only a NEW row may be created this way — never an existing one adopted."""
        row = DevelopmentDialogue.objects.create(
            ref='tumi@example.com::2026', name='Tumi Stranger',
            email='tumi@example.com', is_current=True)
        r = self.c.put('/hris/api/talent/cockpit/', {'people': [
            {'id': row.ref, 'name': 'RENAMED BY BOSS', 'period': '2026',
             'email': 'tumi@example.com'},
        ]}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        row.refresh_from_db()
        self.assertEqual(row.name, 'Tumi Stranger')

    def test_a_managed_email_cannot_authorise_an_unrelated_ref(self):
        """The email arrives in the client's payload, so it may only authorise
        a ref that leads with that same address."""
        r = self.c.put('/hris/api/talent/cockpit/', {'people': [
            {'id': 'ceo@example.com::2026', 'name': 'Not Mine',
             'period': '2026', 'email': 'neo@example.com'},
        ]}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data['saved'], 0)
        self.assertFalse(DevelopmentDialogue.objects.exists())

    def test_a_manager_cannot_mint_a_second_current_dialogue(self):
        """`ref` is unique but `person_key` is not, so a second period opened
        this way would show the same person twice in the grid and the nine-box.
        A further period goes through new_period, which archives the current."""
        DevelopmentDialogue.objects.create(
            ref='neo@example.com::2026', name='Neo Report',
            email='neo@example.com', is_current=True)
        r = self.c.put('/hris/api/talent/cockpit/', {'people': [
            {'id': 'neo@example.com::2099', 'name': 'Neo Report',
             'period': '2099', 'email': 'neo@example.com'},
        ]}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertFalse(DevelopmentDialogue.objects
                         .filter(ref='neo@example.com::2099').exists())
        self.assertEqual(DevelopmentDialogue.objects
                         .filter(email='neo@example.com', is_current=True).count(), 1)

    def test_someone_with_no_reports_still_has_no_cockpit_access(self):
        """The 403 must survive for a person who manages nobody."""
        loner = User.objects.create_user('tumi', email='tumi@example.com')
        c = APIClient()
        c.force_authenticate(loner)
        self.assertEqual(c.get('/hris/api/talent/cockpit/').status_code,
                         status.HTTP_403_FORBIDDEN)


class BlankCompetencyTemplate(TestCase):
    """P2: a record with no `dd` must open as a scorable review, not an empty one."""

    def test_template_has_the_four_competency_sections_unscored(self):
        from hris.dd_template import blank_dd
        dd = blank_dd()
        names = [s['name'] for s in dd['sections']]
        self.assertEqual(len(names), 4, names)
        self.assertIn('Leadership Competency', names)
        rows = [r for s in dd['sections'] for r in s['rows']]
        self.assertGreaterEqual(len(rows), 8)
        self.assertTrue(all(r['perspective'] for r in rows), 'wording was lost')
        self.assertTrue(all(r['manager'] is None and r['employee'] is None
                            for r in rows), 'a template must carry no scores')
        self.assertTrue(all(not r['sbi'] and not r['comments'] for r in rows),
                        "one person's evidence must never leak into another's")
        self.assertTrue(dd['values'])
        self.assertTrue(all(v['self'] is None and v['manager'] is None
                            for v in dd['values']))

    def test_the_template_carries_structure_only_never_a_persons_content(self):
        """It is derived from ONE identified person's real appraisal. Only the
        allowlisted structure fields may come out of it."""
        from hris.dd_template import blank_dd
        dd = blank_dd()
        allowed_row = {'perspective', 'attributes', 'weight', 'sbi', 'target',
                       'start', 'finish', 'manager', 'employee', 'comments'}
        allowed_val = {'value', 'behaviours', 'weight', 'selfText', 'self',
                       'manager', 'raw', 'comments'}
        for sec in dd['sections']:
            self.assertEqual(set(sec.keys()), {'name', 'weight', 'rows'})
            for row in sec['rows']:
                self.assertEqual(set(row.keys()), allowed_row)
        for val in dd['values']:
            self.assertEqual(set(val.keys()), allowed_val)

    def test_needs_template_never_overwrites_real_content(self):
        from hris.dd_template import needs_template
        self.assertTrue(needs_template(None))
        self.assertTrue(needs_template({}))
        self.assertTrue(needs_template({'sections': [], 'values': []}))
        self.assertFalse(needs_template({'sections': [{'name': 'x', 'rows': []}]}))
        self.assertFalse(needs_template('a legacy string'), 'odd shapes are left alone')

    def test_filling_keeps_the_development_plan_and_career_notes(self):
        """The shape the old fixtures never modelled: real content, no sections.

        `dd` also holds the personal development plan, career aspirations,
        priorities, measures, the manager's comments and the rating. Replacing
        it would destroy all of that and answer 200.
        """
        from hris.dd_template import seed_into
        original = {
            'pdp': 'Shadow the reinsurance renewal',
            'careerAspirations': 'Underwriting manager within three years',
            'developmentPriorities': 'Treaty wordings',
            'developmentMeasures': 'Two renewals led end to end',
            'managersComments': 'Ready for more',
            'rating': '3 : Meets Expectations',
            'values': [{'value': 'Botho', 'self': 0.9, 'manager': 0.8}],
        }
        filled = seed_into(dict(original))
        self.assertEqual(len(filled['sections']), 4)
        for key in ('pdp', 'careerAspirations', 'developmentPriorities',
                    'developmentMeasures', 'managersComments', 'rating'):
            self.assertEqual(filled[key], original[key], f'{key} was destroyed')
        self.assertEqual(filled['values'], original['values'],
                         'existing value scores were overwritten')

    def test_the_section_save_path_does_not_wipe_that_content(self):
        """Same wipe, through the code that actually runs on a save."""
        from hris.talent_cockpit_views import _row_from_person
        row = DevelopmentDialogue(ref='e::2026')
        _row_from_person(row, {
            'name': 'Legacy Person', 'period': '2026',
            'dd': {'pdp': 'Keep me', 'careerAspirations': 'Keep me too'},
        })
        self.assertEqual(row.payload['dd']['pdp'], 'Keep me')
        self.assertEqual(row.payload['dd']['careerAspirations'], 'Keep me too')
        self.assertEqual(len(row.payload['dd']['sections']), 4)

    def test_backfill_seeds_only_the_empty_current_records(self):
        from django.core.management import call_command
        from io import StringIO

        empty = DevelopmentDialogue.objects.create(
            ref='a::2026', name='Empty One', is_current=True, payload={})
        scored = DevelopmentDialogue.objects.create(
            ref='b::2026', name='Scored One', is_current=True,
            payload={'dd': {'sections': [{'name': 'Kept', 'rows': [
                {'perspective': 'Kept', 'manager': 0.9}]}], 'values': []}})
        locked = DevelopmentDialogue.objects.create(
            ref='c::2026', name='Locked One', is_current=True, locked=True, payload={})
        planned = DevelopmentDialogue.objects.create(
            ref='p::2026', name='Has A Plan', is_current=True,
            payload={'dd': {'pdp': 'Finish the ACCA', 'rating': '3 : Meets'}})

        call_command('backfill_dd_template', '--apply', stdout=StringIO())

        empty.refresh_from_db(); scored.refresh_from_db(); locked.refresh_from_db()
        self.assertEqual(len(empty.payload['dd']['sections']), 4)
        self.assertEqual(scored.payload['dd']['sections'][0]['name'], 'Kept')
        self.assertEqual(scored.payload['dd']['sections'][0]['rows'][0]['manager'], 0.9)
        self.assertEqual(locked.payload, {}, 'a signed-off period is read-only')
        planned.refresh_from_db()
        self.assertEqual(len(planned.payload['dd']['sections']), 4)
        self.assertEqual(planned.payload['dd']['pdp'], 'Finish the ACCA',
                         'the backfill destroyed a development plan')
        self.assertEqual(planned.payload['dd']['rating'], '3 : Meets')

    def test_backfill_dry_run_writes_nothing(self):
        from django.core.management import call_command
        from io import StringIO

        row = DevelopmentDialogue.objects.create(
            ref='d::2026', name='Empty', is_current=True, payload={})
        call_command('backfill_dd_template', stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.payload, {})


class ExecCreateStillWorks(TestCase):
    """The exec/HR 'all' scope must be untouched by the manager-create branch.

    Two judges read `allowed.covers_email(...)` as a call on the string 'all'.
    It is not: `allowed` is None for an exec, so the branch never runs. This
    pins that, so the next reader does not have to re-derive it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_user('cfo_dd2', email='pganesharajah@example.com')

    def test_exec_can_still_add_a_brand_new_person(self):
        c = APIClient()
        c.force_authenticate(self.cfo)
        r = c.put('/hris/api/talent/cockpit/', {'people': [
            {'id': 'anyone@example.com::2026', 'name': 'Brand New',
             'period': '2026', 'email': 'anyone@example.com'},
        ]}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data['saved'], 1)
        self.assertTrue(DevelopmentDialogue.objects
                        .filter(ref='anyone@example.com::2026').exists())


class SignedOffLegacyRecords(TestCase):
    """What prod actually holds: 34 of the 35 records with no competency
    structure are LOCKED — signed-off history, which must stay read-only.

    The backfill leaves them alone deliberately. The path that serves those
    people is a NEW period, which archives the signed one and opens a fresh,
    unlocked dialogue — and that fresh one must come with the framework.
    """

    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_user('cfo_dd3', email='pganesharajah@example.com')

    def test_a_new_period_off_a_signed_legacy_record_is_scorable(self):
        old = DevelopmentDialogue.objects.create(
            ref='legacy@example.com::FY25', person_key='legacy@example.com',
            name='Legacy Signed', email='legacy@example.com',
            is_current=True, locked=True,
            payload={'name': 'Legacy Signed', 'period': 'FY25',
                     'signoff': {'manager': {'by': 'Someone', 'at': '2025-07-01T09:00'}}})
        c = APIClient()
        c.force_authenticate(self.cfo)
        r = c.post('/hris/api/talent/new-period/',
                   {'ref': old.ref, 'period': 'FY26'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)

        new = (DevelopmentDialogue.objects
               .filter(person_key='legacy@example.com', is_current=True).first())
        self.assertIsNotNone(new)
        self.assertNotEqual(new.ref, old.ref)
        self.assertFalse(new.locked)
        self.assertEqual(len(new.payload['dd']['sections']), 4,
                         'the new period opened with nothing to score')
        self.assertTrue(new.payload['dd']['values'])

        old.refresh_from_db()
        self.assertFalse(old.is_current, 'the signed period must be archived')
        self.assertTrue(old.locked)
        self.assertNotIn('sections', (old.payload.get('dd') or {}),
                         'a signed-off record must not be rewritten')
