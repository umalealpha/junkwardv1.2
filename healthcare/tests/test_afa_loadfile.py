"""Tests for the AFA load-file engine (healthcare/afa_loadfile.py).

Pure functions, no database — these prove AFA's rules are enforced in code
rather than trusted to whoever fills in a spreadsheet.

Synthetic data only — never real member data.
"""
from datetime import date

from django.test import SimpleTestCase

from healthcare import afa_loadfile as afa


def _good_row(**over):
    row = {
        'ADH Policy Number': 'ADH25002', 'Group Name': 'ALPHA DIRECT INSURANCE',
        'Registration Date': '2025-11-01', 'Dependant no': 0,
        'Dependant Type': afa.DEP_TYPE_PRINCIPAL, 'Relationship': afa.REL_MAIN,
        'First Name': 'Sample', 'Surname': 'One', 'Date of Birth': '2000-01-01',
        'IDNumber': '123456', 'Gender': 'MALE', 'Option': 'AD Lite', 'Suspension': 'N',
    }
    row.update(over)
    return row


class LayoutTests(SimpleTestCase):

    def test_layout_is_exactly_35_columns_in_afa_order(self):
        self.assertEqual(len(afa.COLUMNS), 35)
        self.assertEqual(afa.COLUMNS[0], 'ADH Policy Number')
        self.assertEqual(afa.COLUMNS[4], 'Dependant no')
        self.assertEqual(afa.COLUMNS[26], 'Option')
        self.assertEqual(afa.COLUMNS[34], 'Account Type')

    def test_file_is_pipe_delimited_not_comma(self):
        body = afa.render_file([afa.render_row({'ADH Policy Number': 'ADH25002'})])
        self.assertEqual(afa.DELIMITER, '|')
        self.assertEqual(body.splitlines()[0].count('|'), 34)
        self.assertNotIn(',', body.splitlines()[0])

    def test_every_rendered_row_has_35_fields(self):
        rendered = afa.render_row({'ADH Policy Number': 'ADH25002', 'Option': 'AD Lite'})
        self.assertEqual(len(rendered.split('|')), 35)

    def test_a_comma_in_an_address_does_not_shift_the_row(self):
        rendered = afa.render_row({'Address line 1': 'Plot 75, Crescent, Extension 10'})
        self.assertEqual(len(rendered.split('|')), 35)

    def test_a_pipe_inside_a_field_cannot_break_the_row(self):
        rendered = afa.render_row({'Address line 1': 'Plot 75 | Unit B'})
        self.assertEqual(len(rendered.split('|')), 35)
        field = rendered.split('|')[21]
        self.assertNotIn('|', field)
        self.assertEqual(field.split(), ['Plot', '75', 'Unit', 'B'])


class DateRuleTests(SimpleTestCase):

    def test_registration_date_is_forced_to_the_first_of_the_month(self):
        for given in (date(2025, 10, 14), '2025-10-14', '14/10/2025'):
            with self.subTest(given=given):
                self.assertEqual(afa.first_of_month(given), '2025-10-01')

    def test_resignation_date_is_forced_to_the_end_of_the_month(self):
        cases = [
            (date(2026, 3, 1),  '2026-03-31'),
            (date(2026, 2, 14), '2026-02-28'),
            (date(2024, 2, 5),  '2024-02-29'),      # leap year
            (date(2026, 4, 30), '2026-04-30'),
        ]
        for given, expected in cases:
            with self.subTest(given=given):
                self.assertEqual(afa.end_of_month(given), expected)

    def test_unreadable_dates_come_back_blank_not_wrong(self):
        self.assertEqual(afa.first_of_month('not a date'), '')
        self.assertEqual(afa.end_of_month(None), '')


class GenderTests(SimpleTestCase):

    def test_text_forms_are_normalised_to_afas_words(self):
        self.assertEqual(afa.normalise_gender('m'), 'MALE')
        self.assertEqual(afa.normalise_gender('Female'), 'FEMALE')
        self.assertEqual(afa.normalise_gender(''), '')
        self.assertEqual(afa.normalise_gender('unknown'), '')

    def test_graphites_numeric_codes_decode_the_right_way_round(self):
        """1 = Male, 0 = Female — Graphite's convention, confirmed in its source.

        This is the whole live dataset: gender is stored as an integer, never a
        letter. Backwards would flip the sex of every member at AFA.
        """
        for code, expected in ((1, 'MALE'), (0, 'FEMALE'), ('1', 'MALE'), ('0', 'FEMALE')):
            with self.subTest(code=code):
                self.assertEqual(afa.normalise_gender(code), expected)

    def test_an_unknown_code_holds_the_row_rather_than_picking_one(self):
        self.assertEqual(afa.normalise_gender(7), '')
        self.assertIsNotNone(afa.validate_row(_good_row(**{'Gender': afa.normalise_gender(7)})))


class DependantTests(SimpleTestCase):

    def test_wording_maps_onto_afas_closed_columns(self):
        cases = [
            ('Dependant', 'Spouse',          (afa.DEP_TYPE_SPOUSE,    afa.REL_DEP)),
            ('Dependent', 'Child Dependent', (afa.DEP_TYPE_DEPENDANT, afa.REL_CHILD)),
            ('Dependant', 'Adult Dependent', (afa.DEP_TYPE_DEPENDANT, afa.REL_DEP)),
            # Live data really does carry person_type 'Employee' on a dependant
            # row — the relation is the reliable signal, not person_type.
            ('Employee',  'Child Dependent', (afa.DEP_TYPE_DEPENDANT, afa.REL_CHILD)),
        ]
        for person_type, relation, expected in cases:
            with self.subTest(relation=relation):
                self.assertEqual(afa.dependant_labels(person_type, relation), expected)

    def test_an_unmappable_relation_is_blank_so_the_row_gets_held(self):
        self.assertEqual(afa.dependant_labels('Employee', 'wat'), ('', ''))

    def test_a_dependant_carries_the_principals_policy_number_not_its_own(self):
        principal = {'policy_number': 'ADH25002', 'policy_start_date': date(2025, 11, 1),
                     'employee_number': 'E1', 'plan_name': 'AD Lite'}
        dep = {'first_name': 'A', 'surname': 'B', 'relation': 'Spouse',
               'person_type': 'Dependant'}
        row = afa.build_dependant_row(dep, principal, 1,
                                      imed_group_name='G', region_name='R')
        self.assertEqual(row['ADH Policy Number'], 'ADH25002')
        self.assertEqual(row['Dependant no'], 1)
        self.assertEqual(row['Option'], 'AD Lite')      # inherits the principal's plan
        self.assertEqual(row['Account Number'], '')     # the principal pays

    def test_the_principal_is_always_dependant_number_zero(self):
        row = afa.build_principal_row(
            {'policy_number': 'ADH25002', 'plan_name': 'AD Lite'},
            imed_group_name='G', region_name='R')
        self.assertEqual(row['Dependant no'], 0)
        self.assertEqual(row['Dependant Type'], afa.DEP_TYPE_PRINCIPAL)
        self.assertEqual(row['Relationship'], afa.REL_MAIN)


class HoldNeverGuessTests(SimpleTestCase):

    def test_a_clean_row_is_not_held(self):
        self.assertIsNone(afa.validate_row(_good_row()))

    def test_an_unmapped_group_name_is_held_never_guessed(self):
        self.assertIn('iMed', afa.validate_row(_good_row(**{'Group Name': ''})))

    def test_a_plan_outside_afas_five_options_is_held(self):
        self.assertIn('five options', afa.validate_row(_good_row(**{'Option': 'AD Premeir'})))

    def test_a_blank_plan_is_held_and_never_defaulted(self):
        self.assertIsNotNone(afa.validate_row(_good_row(**{'Option': ''})))

    def test_a_reason_outside_afas_list_is_held(self):
        row = _good_row(**{'Resignation reason': 'FED UP', 'Resignation date': '2026-03-31'})
        self.assertIn("AFA's list", afa.validate_row(row))

    def test_a_resignation_reason_without_a_date_is_held(self):
        row = _good_row(**{'Resignation reason': 'RESIGNED'})
        self.assertIn('no resignation date', afa.validate_row(row))

    def test_a_resignation_not_at_month_end_is_held(self):
        row = _good_row(**{'Resignation reason': 'RESIGNED', 'Resignation date': '2026-03-15'})
        self.assertIn('end of a month', afa.validate_row(row))

    def test_a_registration_not_on_the_first_is_held(self):
        self.assertIn('1st of a month',
                      afa.validate_row(_good_row(**{'Registration Date': '2025-11-14'})))

    def test_a_member_with_neither_id_nor_passport_is_held(self):
        self.assertIn('ID or passport', afa.validate_row(_good_row(**{'IDNumber': ''})))

    def test_a_passport_alone_is_enough(self):
        self.assertIsNone(afa.validate_row(
            _good_row(**{'IDNumber': '', 'Passport': 'A377859'})))

    def test_an_unreadable_gender_is_held_not_guessed(self):
        self.assertIn('gender', afa.validate_row(_good_row(**{'Gender': ''})))

    def test_a_dependant_whose_relationship_would_not_map_is_held(self):
        row = _good_row(**{'Dependant no': 2, 'Dependant Type': ''})
        self.assertIn('relationship', afa.validate_row(row))

    def test_all_ten_afa_reason_codes_are_accepted(self):
        for reason in afa.AFA_REASONS:
            with self.subTest(reason=reason):
                row = _good_row(**{'Resignation reason': reason,
                                   'Resignation date': '2026-03-31'})
                self.assertIsNone(afa.validate_row(row))

    def test_all_five_afa_options_are_accepted(self):
        for option in afa.AFA_OPTIONS:
            with self.subTest(option=option):
                self.assertIsNone(afa.validate_row(_good_row(**{'Option': option})))


class SafetyFenceTests(SimpleTestCase):
    """The mass-resignation guard (checklist H25)."""

    COLS = ('policy_number', 'employer_group_id', 'plan_name')

    def test_a_healthy_pull_passes(self):
        afa.check_fence(source_rows=293, known_members=293,
                        columns_seen=self.COLS, replica_lag=2)

    def test_an_empty_pull_aborts_instead_of_resigning_everyone(self):
        with self.assertRaises(afa.LoadFileAborted) as ctx:
            afa.check_fence(source_rows=0, known_members=293,
                            columns_seen=self.COLS, replica_lag=0)
        self.assertIn('cancel the whole scheme', str(ctx.exception))

    def test_a_truncated_pull_aborts(self):
        with self.assertRaises(afa.LoadFileAborted) as ctx:
            afa.check_fence(source_rows=100, known_members=293,
                            columns_seen=self.COLS, replica_lag=0)
        self.assertIn('more than a normal day', str(ctx.exception))

    def test_a_renamed_upstream_column_aborts_rather_than_emitting_blanks(self):
        with self.assertRaises(afa.LoadFileAborted) as ctx:
            afa.check_fence(source_rows=293, known_members=293,
                            columns_seen=('policy_number',), replica_lag=0)
        self.assertIn('schema has probably changed', str(ctx.exception))

    def test_a_badly_lagged_replica_aborts(self):
        with self.assertRaises(afa.LoadFileAborted) as ctx:
            afa.check_fence(source_rows=293, known_members=293,
                            columns_seen=self.COLS, replica_lag=4000)
        self.assertIn('behind', str(ctx.exception))

    def test_normal_daily_churn_does_not_trip_the_fence(self):
        afa.check_fence(source_rows=290, known_members=293,
                        columns_seen=self.COLS, replica_lag=5)

    def test_the_very_first_run_has_no_snapshot_and_still_passes(self):
        afa.check_fence(source_rows=293, known_members=0,
                        columns_seen=self.COLS, replica_lag=None)

    def test_growth_never_trips_the_fence(self):
        afa.check_fence(source_rows=400, known_members=293,
                        columns_seen=self.COLS, replica_lag=1)


class WholeFileTests(SimpleTestCase):

    def test_the_file_carries_the_header_and_one_row_per_life(self):
        principal = {'policy_number': 'ADH25002', 'policy_start_date': date(2025, 11, 1),
                     'plan_name': 'AD Lite', 'first_name': 'Sample', 'surname': 'One',
                     'dob': date(2000, 1, 1), 'id_number': '123456', 'gender': 'M'}
        dep = {'first_name': 'Sample', 'surname': 'Two', 'dob': date(2000, 2, 2),
               'gender': 'F', 'id_number': '1125242',
               'relation': 'Spouse', 'person_type': 'Dependant'}
        rows = [
            afa.render_row(afa.build_principal_row(
                principal, imed_group_name='G', region_name='R')),
            afa.render_row(afa.build_dependant_row(
                dep, principal, 1, imed_group_name='G', region_name='R')),
        ]
        body = afa.render_file(rows)
        lines = body.strip().split('\n')
        self.assertEqual(len(lines), 3)                 # header + 2 lives
        self.assertTrue(all(len(l.split('|')) == 35 for l in lines))
        self.assertTrue(body.endswith('\n'))

    def test_the_same_content_hashes_the_same_and_a_change_does_not(self):
        a = afa.render_row(_good_row())
        b = afa.render_row(_good_row())
        c = afa.render_row(_good_row(**{'Option': 'AD Core'}))
        self.assertEqual(afa.row_hash(a), afa.row_hash(b))
        self.assertNotEqual(afa.row_hash(a), afa.row_hash(c))
