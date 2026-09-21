"""Build Log: stage filter, all-time open count, looks-done yes/no, and one
source of truth for status vs live_at (CFO 13-Sep-2026, bug 7b8d6d5a)."""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import UserProfile
from devlog.day_report import day
from devlog.models import DevCommit, DevDeploy, DevItem
from devlog.tests.test_build_log import CFO, _user


def _item(status, **kw):
    return DevItem.objects.create(asked_text=kw.pop('text', status), status=status,
                                  asked_at=timezone.now(), **kw)


class StatusAndLiveAtAgree(TestCase):
    def test_a_release_stamped_row_cannot_be_set_back_to_waiting(self):
        i = _item('live', live_at=timezone.now())
        i.status = DevItem.Status.WAITING
        i.save(update_fields=['status'])
        i.refresh_from_db()
        self.assertEqual(i.status, DevItem.Status.LIVE)

    def test_live_at_alone_makes_an_open_row_live(self):
        i = _item('building')
        i.live_at = timezone.now()
        i.save(update_fields=['live_at'])
        i.refresh_from_db()
        self.assertEqual(i.status, DevItem.Status.LIVE)


class StageFilter(TestCase):
    def setUp(self):
        _item('asked', text='a'); _item('building', text='b')
        _item('waiting', text='w'); _item('parked', text='p')

    def test_waiting_is_isolated_from_being_built(self):
        d = day(stage='waiting')
        self.assertEqual([r['asked_text'] for r in d['in_flight']], ['w'])
        self.assertEqual(d['still_to_do'], [])

    def test_parked_band_only_on_request(self):
        self.assertEqual(day()['parked'], [])
        self.assertEqual([r['asked_text'] for r in day(stage='parked')['parked']], ['p'])
        self.assertEqual(day(stage='parked')['in_flight'], [])

    def test_junk_stage_is_ignored(self):
        self.assertEqual(len(day(stage='nonsense')['in_flight']), 2)

    def test_open_all_time_ignores_filters(self):
        self.assertEqual(day(stage='parked')['counts']['open_all_time'], 3)


class LooksDone(TestCase):
    def setUp(self):
        self.i = _item('waiting', text='shipped')
        dep = DevDeploy.objects.create(sha='a' * 40, deployed_at=timezone.now())
        DevCommit.objects.create(sha='b' * 40, committed_at=timezone.now(),
                                 deploy=dep, item=self.i)
        _item('building', text='no evidence')

    def test_row_with_released_code_is_put_to_him(self):
        rows = day()['looks_done']
        self.assertEqual([r['asked_text'] for r in rows], ['shipped'])
        self.assertIn('release', rows[0]['reason'])

    def test_his_yes_marks_it_live(self):
        c = APIClient(); c.force_authenticate(_user('pganesharajah', CFO))
        r = c.post('/api/v1/cfo/build-log/confirm/', {'id': str(self.i.id), 'answer': 'yes'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.i.refresh_from_db()
        self.assertEqual(self.i.status, DevItem.Status.LIVE)
        self.assertIsNotNone(self.i.live_at)

    def test_his_no_stops_the_question(self):
        c = APIClient(); c.force_authenticate(_user('pganesharajah', CFO))
        c.post('/api/v1/cfo/build-log/confirm/', {'id': str(self.i.id), 'answer': 'no'}, format='json')
        self.assertEqual(day()['looks_done'], [])
        self.i.refresh_from_db()
        self.assertEqual(self.i.status, DevItem.Status.WAITING)

    def test_nobody_else_can_answer(self):
        c = APIClient(); c.force_authenticate(_user('someone', 'someone@alphadirect.co.bw',
                                                    UserProfile.Title.FINANCE_MANAGER
                                                    if hasattr(UserProfile.Title, 'FINANCE_MANAGER')
                                                    else UserProfile.Title.choices[-1][0]))
        r = c.post('/api/v1/cfo/build-log/confirm/', {'id': str(self.i.id), 'answer': 'yes'}, format='json')
        self.assertEqual(r.status_code, 403)
