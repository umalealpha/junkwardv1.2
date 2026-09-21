"""Tests for the subrogation register loader command.

Includes the load-me-must-reconcile gate: a load that does not foot to the
register's own total is rolled back with nothing written. That gate is proved
here by fault injection — without it, a bad total would reach the database.
"""
import io
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from claims.models import Subrogation, SubrogationPanel
from core.models import Company

HEADER = ("Claim #,Third Party Name,Claim Type,Appointed To,Date Appointed,"
          "Assessor's Fees,Repair Costs,Client's Excess,Towing Fees,Legal Fees,"
          "Salvage Amount,Total Recover,Recovered To Date,Status\n")


def _csv(rows):
    f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8', newline='')
    f.write(HEADER)
    for r in rows:
        f.write(r + '\n')
    f.close()
    return f.name


class RegisterLoaderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create(username='loader', is_superuser=True)
        Company.objects.get_or_create(code='ADIC', defaults={'name': 'Alpha Direct'})

    def _load(self, rows, commit=True):
        out = io.StringIO()
        call_command('load_subrogation_register', _csv(rows),
                     '--company', 'ADIC', *(['--commit'] if commit else []),
                     stdout=out, stderr=out)
        return out.getvalue()

    def test_happy_load_reconciles_and_writes(self):
        rows = [
            # footing components: 1260 + 35437.11 + 7369.07 = 44066.18
            '20150018,Test Party One,Motor Accident Claim,SALBANY & TORTO,2015-07-15,'
            '1260,35437.11,0,0,7369.07,0,44066.18,36000,In progress',
            # keyed only, no components
            'G2026004951,Test Party Two,MOTOR ACCIDENT,Minchin & Kelly,2026-01-10,'
            ',,,,,,10000,0,In progress',
        ]
        out = self._load(rows, commit=True)
        self.assertIn('TIES to the cent', out)
        self.assertEqual(Subrogation.objects.count(), 2)
        by_ref = {s.claim_reference: s for s in Subrogation.objects.all()}
        self.assertEqual(by_ref['20150018'].total_recoverable, Decimal('44066.18'))
        self.assertEqual(by_ref['G2026004951'].total_recoverable, Decimal('10000.00'))
        # graphite-era ref is stamped as the graphite id
        self.assertEqual(by_ref['G2026004951'].graphite_id, 'G2026004951')

    def test_dry_run_writes_nothing(self):
        rows = ['20150018,X,MOTOR ACCIDENT,,,,,,,,,100,0,In progress']
        out = self._load(rows, commit=False)
        self.assertIn('Dry run complete', out)
        self.assertEqual(Subrogation.objects.count(), 0)

    def test_panel_is_created_for_a_law_firm(self):
        self._load(['20150018,X,MOTOR ACCIDENT,SALBANY & TORTO,,,,,,,,100,0,In progress'])
        panel = SubrogationPanel.objects.get()
        self.assertEqual(panel.name, 'Salbany & Torto')
        self.assertEqual(panel.kind, SubrogationPanel.Kind.EXTERNAL_LAWYER)
        self.assertEqual(Subrogation.objects.get().appointed_to, panel)

    def test_payment_method_is_quarantined_not_made_a_panel_member(self):
        self._load(['20150018,X,MOTOR ACCIDENT,Orange Money,,,,,,,,100,0,In progress'])
        self.assertEqual(SubrogationPanel.objects.count(), 0)
        sub = Subrogation.objects.get()
        self.assertIsNone(sub.appointed_to)
        self.assertIn('Orange Money', sub.notes)

    def test_future_date_appointed_is_rejected_and_noted(self):
        self._load(['20150018,X,MOTOR ACCIDENT,,2099-01-01,,,,,,,100,0,In progress'])
        sub = Subrogation.objects.get()
        self.assertIsNone(sub.date_appointed)
        self.assertIn('future', sub.notes.lower())

    def test_status_is_derived_from_the_money(self):
        rows = [
            '20240001,A,MOTOR ACCIDENT,,,,,,,,,100,100,In progress',   # fully recovered
            '20240002,B,MOTOR ACCIDENT,,,,,,,,,100,40,In progress',    # partial
            '20240003,C,MOTOR ACCIDENT,,,,,,,,,100,0,In progress',     # pending
        ]
        self._load(rows)
        s = {x.claim_reference: x.status for x in Subrogation.objects.all()}
        self.assertEqual(s['20240001'], Subrogation.Status.FULLY_RECOVERED)
        self.assertEqual(s['20240002'], Subrogation.Status.PARTIAL)
        self.assertEqual(s['20240003'], Subrogation.Status.PENDING)

    def test_rerun_is_idempotent(self):
        rows = ['20150018,X,MOTOR ACCIDENT,,,,,,,,,100,0,In progress']
        self._load(rows)
        self._load(rows)  # second run
        self.assertEqual(Subrogation.objects.count(), 1)

    def test_blank_ref_rerun_does_not_double_count(self):
        """A row with an unparseable claim number ('TBA') must dedupe on re-run
        too — otherwise every --commit re-load duplicates it and inflates the
        book. Keyed on third party + amount + company."""
        rows = ['TBA,Reddys Depot,MOTOR ACCIDENT,,,,,,,,,6857.31,0,In progress']
        self._load(rows)
        self._load(rows)  # second run
        self.assertEqual(Subrogation.objects.count(), 1)
        self.assertEqual(Subrogation.objects.first().claim_reference, '(none)')

    def test_reconciliation_gate_rolls_back_a_bad_total(self):
        """Fault injection: force one row's stored total to diverge from its
        keyed figure. The gate must raise and leave the DB empty."""
        rows = ['20150018,X,MOTOR ACCIDENT,,,,,,,,,100,0,In progress']
        real = Subrogation.total_recoverable

        # Patch the property so the loaded sum no longer matches the keyed sum.
        bad = property(lambda self: Decimal('999999.99'))
        with mock.patch.object(Subrogation, 'total_recoverable', bad):
            with self.assertRaises(CommandError):
                self._load(rows, commit=True)
        # rolled back — nothing written
        self.assertEqual(Subrogation.objects.count(), 0)
        # sanity: the real property is untouched afterwards
        self.assertIsInstance(real, property)
