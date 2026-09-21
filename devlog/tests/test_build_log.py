"""The CFO's build log (CFO 2026-09-09).

Two things must not break: the lock, and the honesty of "finished".
"""
import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import UserProfile, get_user_profile
from core.permissions import is_the_cfo
from devlog.day_report import LOCAL_OFFSET, day, local_day_bounds
from devlog.models import DevCommit, DevDeploy, DevItem

CFO = 'pganesharajah@alphadirect.co.bw'


def _user(username, email, title=UserProfile.Title.CFO, superuser=False):
    """A real login WITH a profile. The profile is not created by a signal, so
    a fixture that forgets it makes every gate test pass for the wrong reason —
    the guard would look correct while refusing everybody."""
    u = User.objects.create_user(username=username, email=email,
                                 password='x', is_superuser=superuser)
    UserProfile.objects.create(user=u, role=UserProfile.Role.choices[0][0],
                               title=title, is_active=True)
    u.refresh_from_db()
    return u


class GateTests(TestCase):
    def test_the_cfos_own_account_is_let_in(self):
        u = _user('pganesharajah', CFO, UserProfile.Title.CFO)
        self.assertTrue(is_the_cfo(u))

    def test_the_shared_exco_mailbox_is_refused_even_though_it_is_titled_cfo(self):
        """excoboard@ carries the cfo title in Omni. 'My eyes only' and a
        shared mailbox are contradictory — this is the whole point of the gate,
        and the CFO chose it knowing he must sign in as himself."""
        u = _user('excoboard', 'excoboard@alphadirect.co.bw', UserProfile.Title.CFO)
        self.assertFalse(is_the_cfo(u))

    def test_a_superuser_is_refused(self):
        """The QC robots and the Super Admin ARE superusers; the CFO's own SSO
        account is NOT. A superuser arm would admit the wrong people and still
        lock him out."""
        u = _user('qcbot', 'qc@alphadirect.co.bw', UserProfile.Title.CFO, superuser=True)
        self.assertFalse(is_the_cfo(u))

    def test_his_email_without_the_cfo_title_is_refused(self):
        u = _user('impostor', CFO, UserProfile.Title.FINANCE_MANAGER)
        self.assertFalse(is_the_cfo(u))

    def test_a_deactivated_profile_is_refused(self):
        u = _user('pganesharajah', CFO, UserProfile.Title.CFO)
        p = get_user_profile(u)
        p.is_active = False
        p.save(update_fields=['is_active'])
        u.refresh_from_db()
        self.assertFalse(is_the_cfo(u))

    def test_the_username_arm_works_when_the_email_differs(self):
        """His SSO email has changed before. The username arm is the fallback."""
        u = _user('pganesharajah', 'pg@alphadirect.co.bw', UserProfile.Title.CFO)
        self.assertTrue(is_the_cfo(u))

    def test_anonymous_is_refused(self):
        self.assertFalse(is_the_cfo(None))


class DayWindowTests(TestCase):
    def test_the_day_is_botswanas_not_the_servers(self):
        """The box runs UTC. A day ending at 02:00 local would file the last two
        hours of a late session under tomorrow."""
        start, end = local_day_bounds(dt.date(2026, 9, 9))
        self.assertEqual(start.isoformat(), '2026-09-08T22:00:00+00:00')
        self.assertEqual(end.isoformat(), '2026-09-09T22:00:00+00:00')
        self.assertEqual(LOCAL_OFFSET, dt.timedelta(hours=2))


class DayReportTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def _item(self, text, status=DevItem.Status.ASKED, asked=None, live=None, key=''):
        return DevItem.objects.create(
            asked_text=text, asked_at=asked or self.now, status=status,
            live_at=live, client_key=key)

    def test_an_empty_log_reports_zeroes_not_a_crash(self):
        r = day()
        self.assertEqual(r['counts']['went_live_today'], 0)
        self.assertEqual(r['counts']['still_open'], 0)

    def test_what_went_live_today(self):
        self._item('build the thing', DevItem.Status.LIVE, live=self.now)
        r = day()
        self.assertEqual(r['counts']['went_live_today'], 1)
        self.assertEqual(r['went_live'][0]['asked_text'], 'build the thing')

    def test_open_work_from_earlier_days_still_shows_under_what_to_finish(self):
        """'What to finish' is not limited to today, or a thing asked on Monday
        disappears on Tuesday."""
        self._item('older ask', DevItem.Status.ASKED,
                   asked=self.now - dt.timedelta(days=4))
        r = day()
        self.assertEqual(r['counts']['still_open'], 1)
        self.assertEqual(r['still_to_do'][0]['asked_text'], 'older ask')

    def test_building_and_waiting_are_in_flight_not_finished(self):
        self._item('half done', DevItem.Status.BUILDING)
        self._item('built not shipped', DevItem.Status.WAITING)
        r = day()
        self.assertEqual(len(r['in_flight']), 2)
        self.assertEqual(r['counts']['went_live_today'], 0)

    def test_a_deployed_commit_with_no_request_is_shown_not_hidden(self):
        """Capture only happens through /goal, /code, /lane-b and /fabe. If the
        rest were dropped the page would look complete and be wrong."""
        d = DevDeploy.objects.create(sha='a' * 40, deployed_at=self.now, commit_count=1)
        DevCommit.objects.create(sha='b' * 40, subject='fix something nobody logged',
                                 committed_at=self.now, deploy=d)
        r = day()
        self.assertEqual(r['counts']['shipped_without_a_request'], 1)
        self.assertEqual(r['unlinked_commits'][0]['subject'],
                         'fix something nobody logged')

    def test_a_linked_commit_is_not_counted_as_unrecorded(self):
        item = self._item('asked for this', key='K1')
        d = DevDeploy.objects.create(sha='c' * 40, deployed_at=self.now, commit_count=1)
        DevCommit.objects.create(sha='d' * 40, subject='did it', committed_at=self.now,
                                 deploy=d, item=item)
        self.assertEqual(day()['counts']['shipped_without_a_request'], 0)

    def test_recording_since_is_reported_so_gaps_are_not_mistaken_for_silence(self):
        first = self.now - dt.timedelta(days=10)
        self._item('the first ask ever', asked=first)
        self.assertEqual(day()['recording_since'], first)


class FilterOptionTests(TestCase):
    def test_the_area_dropdown_has_no_duplicates(self):
        """Django adds the model's Meta ordering to the SELECT, so .distinct()
        silently applies to (area, asked_at) and every row survives. Nine
        requests in one area listed the area nine times."""
        for n in range(4):
            DevItem.objects.create(asked_text=f'ask {n}', asked_at=timezone.now(),
                                   area='review-engine', source='chat')
        opts = day()['filter_options']
        self.assertEqual(opts['areas'], ['review-engine'])
        self.assertEqual(opts['sources'], ['chat'])


class DeployRecordTests(TestCase):
    def test_only_a_deploy_marks_something_live(self):
        """A skill can claim it built something; a deploy is the only thing that
        proves it reached prod."""
        from django.core.management import call_command
        item = DevItem.objects.create(asked_text='ship me', asked_at=timezone.now(),
                                      client_key='ITEM-1')
        self.assertIsNone(item.live_at)
        # The trailer goes on its OWN LINE in the body, which is where git puts a
        # trailer and where every real commit carries it. This test used to bury
        # it mid-subject; that was accepted only because the parser searched
        # unanchored text, which is the bug that let a sentence ABOUT trailers
        # hijack the link (fixed 11-Sep-2026).
        log = '\x1f'.join(['e' * 40, 'Someone', '1757400000', 'feat: done',
                           'Dev-Item: ITEM-1'])
        call_command('devlog_deploy', sha='f' * 40, prev='0' * 40, log=log)
        item.refresh_from_db()
        self.assertEqual(item.status, DevItem.Status.LIVE)
        self.assertIsNotNone(item.live_at)
        self.assertIsNotNone(item.deploy)

    def test_a_commit_without_a_trailer_is_never_guessed_onto_an_item(self):
        """Matching on words would invent a finished piece of work nobody did."""
        from django.core.management import call_command
        DevItem.objects.create(asked_text='build the reminder emails',
                               asked_at=timezone.now(), client_key='ITEM-2')
        log = '\x1f'.join(['1' * 40, 'Someone', '1757400000',
                           'fix: the reminder emails'])   # same words, no trailer
        call_command('devlog_deploy', sha='2' * 40, log=log)
        self.assertIsNone(DevCommit.objects.get(sha='1' * 40).item)
        self.assertEqual(DevItem.objects.get(client_key='ITEM-2').status,
                         DevItem.Status.ASKED)

    def test_recording_the_same_deploy_twice_is_a_no_op(self):
        from django.core.management import call_command
        log = '\x1f'.join(['3' * 40, 'A', '1757400000', 'one'])
        call_command('devlog_deploy', sha='4' * 40, log=log)
        call_command('devlog_deploy', sha='4' * 40, log=log)
        self.assertEqual(DevDeploy.objects.filter(sha='4' * 40).count(), 1)
        self.assertEqual(DevCommit.objects.count(), 1)
