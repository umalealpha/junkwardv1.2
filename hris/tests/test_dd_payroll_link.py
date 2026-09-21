"""A Development Dialogue saved in the cockpit must know WHO it is about.

Found 30 Jul 2026 while proving out Unami's request: every one of the 7 live
dialogues had `employee_id = None`, including the CFO's own. `_row_from_person`
set name/department/scores but never `email` or `employee`, so a dialogue created
in the cockpit came out orphaned from payroll. Two things break when it does:

  * `my-dialogue` looks the caller up BY EMAIL — an orphaned row means the person
    cannot open their own dialogue, and
  * the 9-box overlay matches on employee_id or email — an orphaned row can never
    reach the grid, which is exactly the "link the results to the nine-box grid"
    complaint.

The cockpit app mints its person id as `<email>::<period>`, so the ref is a
reliable fallback when the person object carries no email of its own.
"""
from __future__ import annotations

from django.test import TestCase

from hris.talent_cockpit_models import DevelopmentDialogue
from hris.talent_cockpit_views import _match_employee, _person_email, _row_from_person
from payroll.models import Employee


class PersonEmailTests(TestCase):
    def test_reads_an_explicit_email_field(self):
        self.assertEqual(_person_email({'email': 'a@alphadirect.co.bw'}, ''),
                         'a@alphadirect.co.bw')

    def test_falls_back_to_the_ref_which_encodes_the_email(self):
        ref = 'pganesharajah@alphadirect.co.bw::1_July_2025_to_30_June_2026'
        self.assertEqual(_person_email({}, ref), 'pganesharajah@alphadirect.co.bw')

    def test_reads_a_nested_details_email(self):
        p = {'dd': {'details': {'email': 'b@alphadirect.co.bw'}}}
        self.assertEqual(_person_email(p, ''), 'b@alphadirect.co.bw')

    def test_a_ref_without_an_email_yields_nothing(self):
        self.assertEqual(_person_email({}, '5'), '')
        self.assertEqual(_person_email({}, ''), '')


class MatchEmployeeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(full_name='Unique Person', status='active',
                                          email='unique@alphadirect.co.bw',
                                          employee_number='DD-1')
        cls.twin_a = Employee.objects.create(full_name='Same Name', status='active',
                                             email='twin1@alphadirect.co.bw',
                                             employee_number='DD-2')
        cls.twin_b = Employee.objects.create(full_name='Same Name', status='active',
                                             email='twin2@alphadirect.co.bw',
                                             employee_number='DD-3')

    def test_matches_on_email(self):
        self.assertEqual(_match_employee('UNIQUE@alphadirect.co.bw', ''), self.emp)

    def test_matches_on_an_unambiguous_name(self):
        self.assertEqual(_match_employee('', 'Unique Person'), self.emp)

    def test_refuses_to_guess_between_duplicate_names(self):
        """The roster has real duplicate full names. Attaching an appraisal to the
        wrong person is worse than leaving it unlinked."""
        self.assertIsNone(_match_employee('', 'Same Name'))

    def test_no_match_is_none_not_an_exception(self):
        self.assertIsNone(_match_employee('nobody@x.com', 'Nobody At All'))


class RowFromPersonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(full_name='Prathap Ganesharajah', status='active',
                                          email='pganesharajah@alphadirect.co.bw',
                                          employee_number='DD-CFO')

    def test_save_links_email_and_payroll_record(self):
        ref = 'pganesharajah@alphadirect.co.bw::1_July_2025_to_30_June_2026'
        row = DevelopmentDialogue(ref=ref)
        _row_from_person(row, {'name': 'Prathap Ganesharajah', 'dept': 'Finance & Planning',
                               'period': '1 July 2025 to 30 June 2026', 'performance': 0.72})
        self.assertEqual(row.email, 'pganesharajah@alphadirect.co.bw')
        self.assertEqual(row.employee, self.emp)

    def test_an_existing_link_is_never_overwritten(self):
        other = Employee.objects.create(full_name='Someone Else', status='active',
                                        email='else@alphadirect.co.bw', employee_number='DD-X')
        row = DevelopmentDialogue(ref='x::y', employee=other)
        _row_from_person(row, {'name': 'Prathap Ganesharajah',
                               'email': 'pganesharajah@alphadirect.co.bw'})
        self.assertEqual(row.employee, other)      # HR's manual attach wins

    def test_unmatched_person_still_saves(self):
        row = DevelopmentDialogue(ref='ghost::2026')
        _row_from_person(row, {'name': 'Not On Payroll', 'dept': 'Nowhere'})
        self.assertIsNone(row.employee)
        self.assertEqual(row.name, 'Not On Payroll')
