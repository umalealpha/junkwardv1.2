"""Development Dialogue full-screen live-review rebuild — backend guarantees.

Board dd515fa8 [DD-LIVE], CFO 21-Sep-2026. These pin the four backend rules the
brief calls out by number. Every one is written to FAIL against the code as it
stands before the rebuild, so a green run proves the change, not the wiring.

  D2  overall % follows the MANAGER score, not the employee self-score
  T1  a section-by-section save merges — it never wipes the other sections,
      and a save that silently drops rows is refused
  D4  the moderator records a challenge ALONGSIDE a signed record; the original
      is untouched and stays locked
  T9  one canonical nine-box list is served, so the grids cannot disagree
"""
from __future__ import annotations

import copy

from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
from django.test import TestCase

from hris.talent_cockpit_models import DevelopmentDialogue


def _payload_two_sections():
    """A minimal dialogue: two competency sections, one scored row each, plus a
    manager score that DIFFERS from the employee self-score so D2 is testable."""
    return {
        'dd': {
            'sections': [
                {'title': 'Leadership',
                 'rows': [{'title': 'Leads the team', 'employee': 0.4,
                           'manager': 0.9, 'weight': 1.0, 'sbi': 'orig-A'}]},
                {'title': 'Business',
                 'rows': [{'title': 'Delivers results', 'employee': 0.5,
                           'manager': 0.5, 'weight': 1.0, 'sbi': 'orig-B'}]},
            ],
            'values': [],
        },
    }


class DDLiveReviewBackend(TestCase):
    @classmethod
    def setUpTestData(cls):
        # pganesharajah local-part is on the exec/HR see-all allowlist → scope 'all'
        cls.exec_user = User.objects.create_user(
            'cfo_dd', email='pganesharajah@alphadirect.co.bw')

    def setUp(self):
        self.c = APIClient()
        self.c.force_authenticate(self.exec_user)

    # ---- D2 : the headline follows the manager's score ----------------------
    def test_overall_follows_manager_score_not_employee(self):
        from hris.talent_cockpit_views import _recompute
        payload = _payload_two_sections()   # manager 0.9/0.5, employee 0.4/0.5
        _recompute(payload)
        # manager-weighted section B = 0.9*1 + 0.5*1 = 1.4 → overall 140.0
        # (employee-driven would be 0.4 + 0.5 = 0.9 → 90.0)
        self.assertEqual(payload['dd']['sectionB'], 1.4)
        self.assertEqual(payload['overall'], 140.0)

    # ---- T1 : section save merges, never wipes -------------------------------
    def test_section_save_keeps_the_other_sections(self):
        row = DevelopmentDialogue.objects.create(
            ref='p1::FY2026', name='Person One', period='FY2026',
            email='p1@alphadirect.co.bw', payload=_payload_two_sections())
        # save ONLY section 1 (Business), changing its manager score + narrative
        resp = self.c.post('/hris/api/talent/save-section/', {
            'ref': 'p1::FY2026', 'part': 'section', 'index': 1,
            'rows': [{'manager': 0.8, 'sbi': 'edited-B'}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        row.refresh_from_db()
        secs = row.payload['dd']['sections']
        # section 0 is byte-identical to what was stored
        self.assertEqual(secs[0]['rows'][0]['sbi'], 'orig-A')
        self.assertEqual(secs[0]['rows'][0]['manager'], 0.9)
        # section 1 took the edit; the employee self-score is preserved
        self.assertEqual(secs[1]['rows'][0]['sbi'], 'edited-B')
        self.assertEqual(secs[1]['rows'][0]['manager'], 0.8)
        self.assertEqual(secs[1]['rows'][0]['employee'], 0.5)

    def test_section_save_refuses_a_silent_row_drop(self):
        stored = _payload_two_sections()
        stored['dd']['sections'][0]['rows'].append(
            {'title': 'Second leadership row', 'employee': 0.3,
             'manager': 0.6, 'weight': 1.0, 'sbi': 'orig-A2'})
        DevelopmentDialogue.objects.create(
            ref='p2::FY2026', name='Person Two', period='FY2026',
            email='p2@alphadirect.co.bw', payload=copy.deepcopy(stored))
        # section 0 has 2 rows stored; sending 1 with no explicit remove → refused
        resp = self.c.post('/hris/api/talent/save-section/', {
            'ref': 'p2::FY2026', 'part': 'section', 'index': 0,
            'rows': [{'manager': 0.7}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.content)
        row = DevelopmentDialogue.objects.get(ref='p2::FY2026')
        self.assertEqual(len(row.payload['dd']['sections'][0]['rows']), 2)  # nothing dropped

    # ---- T2 : a bulk save never deletes an omitted person --------------------
    def test_bulk_save_never_deletes_omitted_person(self):
        DevelopmentDialogue.objects.create(
            ref='keep::FY2026', name='Keep Me', period='FY2026',
            email='keep@alphadirect.co.bw', payload=_payload_two_sections())
        # a filtered/partial cockpit save that does not mention keep::FY2026
        resp = self.c.put('/hris/api/talent/cockpit/', {'people': [
            {'id': 'other::FY2026', 'name': 'Other', 'period': 'FY2026'}]},
            format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(DevelopmentDialogue.objects.filter(ref='keep::FY2026').exists())

    # ---- D4 : moderator challenges alongside, original sealed ----------------
    def test_moderator_challenge_leaves_original_and_lock_intact(self):
        original = _payload_two_sections()
        row = DevelopmentDialogue.objects.create(
            ref='p3::FY2026', name='Person Three', period='FY2026',
            email='p3@alphadirect.co.bw', locked=True,
            payload=copy.deepcopy(original))
        resp = self.c.post('/hris/api/talent/moderator-challenge/', {
            'ref': 'p3::FY2026',
            'reason': 'Leadership rated too high given the missed Q3 target.',
            'sections': [{'index': 0, 'rows': [{'manager': 0.5}]}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        row.refresh_from_db()
        # the record is still sealed and the manager's original scores are untouched
        self.assertTrue(row.locked)
        self.assertEqual(row.payload['dd']['sections'][0]['rows'][0]['manager'], 0.9)
        # the challenge is stored as its own layer, attributed and reasoned
        ch = row.payload.get('challenge')
        self.assertIsNotNone(ch)
        self.assertIn('Leadership rated too high', ch.get('reason', ''))
        self.assertEqual(ch['sections'][0]['rows'][0]['manager'], 0.5)
        self.assertTrue(ch.get('by'))
        self.assertTrue(ch.get('at'))

    def test_moderator_challenge_does_not_unlock(self):
        row = DevelopmentDialogue.objects.create(
            ref='p4::FY2026', name='Person Four', period='FY2026',
            email='p4@alphadirect.co.bw', locked=True,
            payload=_payload_two_sections())
        self.c.post('/hris/api/talent/moderator-challenge/', {
            'ref': 'p4::FY2026', 'reason': 'x',
            'sections': [{'index': 0, 'rows': [{'manager': 0.5}]}]}, format='json')
        row.refresh_from_db()
        self.assertTrue(row.locked)

    def test_section_save_survives_a_section_with_no_rows_key(self):
        payload = {'dd': {'sections': [{'title': 'Leadership'}], 'values': []}}  # no 'rows' key
        DevelopmentDialogue.objects.create(
            ref='p5::FY2026', name='Person Five', period='FY2026',
            email='p5@alphadirect.co.bw', payload=payload)
        resp = self.c.post('/hris/api/talent/save-section/', {
            'ref': 'p5::FY2026', 'part': 'section', 'index': 0,
            'rows': [{'manager': 0.6, 'sbi': 'added'}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        row = DevelopmentDialogue.objects.get(ref='p5::FY2026')
        self.assertEqual(row.payload['dd']['sections'][0]['rows'][0]['sbi'], 'added')

    def test_second_moderator_challenge_keeps_the_first(self):
        row = DevelopmentDialogue.objects.create(
            ref='p6::FY2026', name='Person Six', period='FY2026',
            email='p6@alphadirect.co.bw', locked=True, payload=_payload_two_sections())
        self.c.post('/hris/api/talent/moderator-challenge/', {
            'ref': 'p6::FY2026', 'reason': 'first challenge',
            'sections': []}, format='json')
        self.c.post('/hris/api/talent/moderator-challenge/', {
            'ref': 'p6::FY2026', 'reason': 'second challenge',
            'sections': []}, format='json')
        row.refresh_from_db()
        reasons = [c['reason'] for c in row.payload.get('challenges', [])]
        self.assertEqual(reasons, ['first challenge', 'second challenge'])

    # ---- T9 : one canonical nine-box list ------------------------------------
    def test_nine_box_labels_are_canonical_and_nine(self):
        resp = self.c.get('/hris/api/talent/nine-box-labels/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        labels = resp.data.get('labels')
        self.assertEqual(len(labels), 9)
        names = {l['name'] for l in labels}
        self.assertEqual(names, {
            'Star', 'High Potential', 'Rough Diamond', 'High Performer',
            'Core Player', 'Inconsistent', 'Solid Specialist',
            'Underperformer', 'Talent Risk'})
