"""Seeding the Brand/Model dropdowns from Graphite — 17-Sep-2026.

Bharath: "Vehicle details - Brand and Model is currently not working. We must
have a drop down of all make and models. We have this in graphite."

Graphite's `vehicle.make` is free text typed by underwriters over several
years, so the same manufacturer arrives as TOYOTA, toyota and Toyota, next to
blanks and placeholders. Seeding it raw would give the yard three Toyota rows
and a dropdown entry called "n/a", so the cleaner is the part worth testing.

The Graphite read itself is not tested here — it is a live replica this suite
must never touch. `clean()` is the pure function that decides what lands.
"""
from __future__ import annotations

from django.test import TestCase

from salvage.management.commands.seed_vehicle_makes import clean, tally


class CleanMakeTests(TestCase):

    def test_case_variants_collapse_to_one_row(self):
        """Three spellings of one manufacturer must not become three makes."""
        self.assertEqual(clean('TOYOTA'), 'Toyota')
        self.assertEqual(clean('toyota'), 'Toyota')
        self.assertEqual(clean('Toyota'), 'Toyota')

    def test_initials_are_not_title_cased(self):
        """GWM is not 'Gwm'. Both spellings must also land on one row."""
        self.assertEqual(clean('GWM'), 'GWM')
        self.assertEqual(clean('gwm'), 'GWM')
        self.assertEqual(clean('BMW'), 'BMW')
        self.assertEqual(clean('bmw'), 'BMW')

    def test_aliases_land_on_the_make_the_yard_already_has(self):
        """The legacy import seeded Title-Case makes. 'VW' must join
        Volkswagen, not sit beside it as a second brand."""
        self.assertEqual(clean('VW'), 'Volkswagen')
        self.assertEqual(clean('vw'), 'Volkswagen')
        self.assertEqual(clean('MERCEDES BENZ'), 'Mercedes-Benz')
        self.assertEqual(clean('LANDROVER'), 'Land Rover')
        self.assertEqual(clean('Land Rover'), 'Land Rover')

    def test_a_real_model_name_is_not_aliased_into_a_brand(self):
        """clean() runs over models too. Range Rover is a MODEL — aliasing it
        to Land Rover would file it as "Land Rover Land Rover"."""
        self.assertEqual(clean('Range Rover'), 'Range Rover')
        self.assertEqual(clean('RANGE ROVER'), 'Range Rover')

    def test_multi_word_names_survive(self):
        self.assertEqual(clean('Land Rover'), 'Land Rover')
        self.assertEqual(clean('LAND ROVER'), 'Land Rover')

    def test_already_mixed_case_is_left_alone(self):
        """Model names like iX3 have no safe re-casing."""
        self.assertEqual(clean('iX3'), 'iX3')
        self.assertEqual(clean('e-Golf'), 'e-Golf')

    def test_whitespace_is_squeezed(self):
        self.assertEqual(clean('  Land   Rover '), 'Land Rover')

    def test_placeholders_are_dropped(self):
        for junk in ('', '   ', '-', 'n/a', 'N/A', 'none', 'Unknown', 'TBC',
                     '0', 'x', '.'):
            with self.subTest(junk=junk):
                self.assertIsNone(clean(junk))

    def test_single_characters_are_dropped(self):
        """A stray keystroke must not become a dropdown entry."""
        self.assertIsNone(clean('T'))
        self.assertIsNone(clean('9'))

    def test_none_is_handled(self):
        self.assertIsNone(clean(None))

    def test_a_real_make_survives(self):
        self.assertEqual(clean('mercedes-benz'), 'Mercedes-Benz')


class TallyTests(TestCase):
    """What actually reaches the dropdown once the rare typos are filtered."""

    def rows(self, *triples):
        return [{'make': mk, 'model': md, 'n': n} for mk, md, n in triples]

    def test_counts_are_added_up_after_cleaning_not_before(self):
        """'TOYOTA' x2 and 'toyota' x2 is ONE make on four vehicles.

        Counting before cleaning would leave two makes of two each, and with a
        floor of three BOTH would be thrown away — losing the commonest car in
        the country.
        """
        out = tally(self.rows(('TOYOTA', 'Hilux', 2), ('toyota', 'Hilux', 2)),
                    min_make=3, min_model=2)
        self.assertEqual(set(out), {'Toyota'})
        self.assertEqual(out['Toyota'], {'Hilux'})

    def test_a_one_off_typo_does_not_reach_the_dropdown(self):
        out = tally(self.rows(('Toyota', 'Hilux', 900), ('Toyoat', 'Hilux', 1)),
                    min_make=3, min_model=2)
        self.assertEqual(set(out), {'Toyota'})

    def test_a_rare_model_is_dropped_but_its_make_stays(self):
        out = tally(self.rows(('Nissan', 'Magnite', 40), ('Nissan', 'Magnitte', 1)),
                    min_make=3, min_model=2)
        self.assertEqual(set(out), {'Nissan'})
        self.assertEqual(out['Nissan'], {'Magnite'})

    def test_a_make_with_no_usable_model_still_reaches_the_dropdown(self):
        """Brand is the field Bharath needs; a blank model must not cost him
        the make."""
        out = tally(self.rows(('Isuzu', None, 12), ('Isuzu', 'n/a', 3)),
                    min_make=3, min_model=2)
        self.assertEqual(set(out), {'Isuzu'})
        self.assertEqual(out['Isuzu'], set())

    def test_junk_makes_never_appear(self):
        out = tally(self.rows(('n/a', 'x', 500), ('-', '-', 40)),
                    min_make=3, min_model=2)
        self.assertEqual(out, {})

    def test_a_missing_count_column_is_treated_as_one_vehicle(self):
        out = tally([{'make': 'Ford', 'model': 'Ranger'}] * 1,
                    min_make=1, min_model=1)
        self.assertEqual(out, {'Ford': {'Ranger'}})
