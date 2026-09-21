"""
Self-updating ROPA drift logic (core.ropa_auto) — CFO 2026-07-24.

Guards the keyword reconciliation that decides which live processing activities
are "Recorded" in the DPO's uploaded ROPA vs "To record" (drift). The bugs these
tests lock out (Fable K7): free-text rows cross-contaminating the match —
"driver's licence" falsely recording telematics, "AWS Mumbai" falsely recording
AI. Pure logic — the DB-touching helpers are mocked, so no fixtures needed.
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from core import ropa_auto


def _pad(*rows: str) -> list[str]:
    """Mirror _validated_activities' blob padding (space both ends, lowercased)."""
    return [' ' + r.lower() + ' ' for r in rows]


class BuildLiveRopaTests(SimpleTestCase):

    def _build(self, blobs):
        at = '2026-07-24T00:00:00' if blobs else None
        with mock.patch.object(ropa_auto, '_live_counts', return_value={}), \
             mock.patch.object(ropa_auto, '_validated_activities', return_value=(blobs, at)):
            return ropa_auto.build_live_ropa()

    def test_no_workbook_leaves_everything_unknown(self):
        out = self._build([])
        self.assertFalse(out['has_validated_ropa'])
        self.assertTrue(all(a['recorded'] is None for a in out['activities']))
        self.assertEqual(out['summary']['needs_recording'], 0)
        self.assertEqual(out['summary']['recorded'], 0)

    def test_kyc_row_does_not_falsely_record_telematics(self):
        # A KYC row mentioning a driver's licence must NOT mark Nexus telematics
        # as recorded — that would hide the exact drift this feature exists for.
        out = self._build(_pad("Omang, passport, driver's licence — identity verification"))
        rec = {a['key']: a['recorded'] for a in out['activities']}
        self.assertTrue(rec['kyc'])
        self.assertFalse(rec['telematics'])

    def test_mumbai_transfer_does_not_falsely_record_ai(self):
        # "AWS Mumbai" contains the substring "ai " — the two-sided ' ai ' guard
        # must stop it recording the AI activity. Payroll IS recorded.
        out = self._build(_pad('Payroll: employee salary — hosted AWS Mumbai transfer'))
        rec = {a['key']: a['recorded'] for a in out['activities']}
        self.assertTrue(rec['payroll'])
        self.assertFalse(rec['ai'])

    def test_needs_recording_counts_unmatched_activities(self):
        # Only claims recorded → the rest are drift ("To record").
        out = self._build(_pad('Claims handling — incident and payout data'))
        rec = {a['key']: a['recorded'] for a in out['activities']}
        self.assertTrue(rec['claims'])
        self.assertEqual(out['summary']['recorded'], 1)
        self.assertEqual(out['summary']['needs_recording'], len(out['activities']) - 1)


class CountGuardTests(SimpleTestCase):
    def test_failing_counter_returns_none_and_does_not_raise(self):
        def boom():
            raise RuntimeError('db down')
        with self.assertLogs('core.ropa_auto', level='WARNING'):
            self.assertIsNone(ropa_auto._count(boom))

    def test_good_counter_returns_int(self):
        self.assertEqual(ropa_auto._count(lambda: 7), 7)
