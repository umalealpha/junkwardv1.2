"""A `Dev-Item:` trailer must actually flip the build item to live (CFO 2026-09-11).

Everything else about the release recording worked — the deploy ran the recorder,
the recorder called devlog_deploy, devlog_deploy wrote the release row and the
marker advanced — and the "Finished today" band STILL showed nothing, because the
one step that links a commit to the CFO's request could never succeed:

  * `Dev-Item:` is a TRAILER. Git puts trailers at the END of a commit message,
    i.e. in the body (%b), never in the subject (%s).
  * The recorder sent `--format=…%s` — subject only.
  * devlog_deploy searched only that subject.

So the trailer was invisible by construction. Proven live: PR #888 merged with
`Dev-Item: cfo-2026-09-11-buildlog-finished` in the commit, the deploy recorded
the release, and the item stayed un-live.

These tests use the real shape of a GitHub squash merge — subject ending in
"(#888)", trailer at the bottom of the body — and go red against the old
subject-only behaviour.
"""
import sys
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from devlog.models import DevItem

UNIT = '\x1f'
RECORD = '\x1e'


def commit(sha, subject, body=''):
    """One record in the format record-release.sh sends."""
    return f'{sha}{UNIT}Someone{UNIT}{int(timezone.now().timestamp())}{UNIT}{subject}{UNIT}{body}{RECORD}'


SQUASH_BODY = """The Build Log's "Finished today" band was still empty.

Some detail about the change, over several
lines, the way a real PR body reads.

Dev-Item: cfo-2026-09-11-buildlog-finished

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"""


class DevItemTrailerLinksTests(TestCase):
    def setUp(self):
        self.item = DevItem.objects.create(
            client_key='cfo-2026-09-11-buildlog-finished',
            asked_text='it dosnt update the development board',
            title='build log',
            asked_at=timezone.now(),
        )

    def record(self, log, sha='a' * 40):
        out = StringIO()
        call_command('devlog_deploy', sha=sha, prev='b' * 40, log=log, stdout=out)
        self.item.refresh_from_db()
        return out.getvalue()

    def test_a_trailer_in_the_body_goes_live(self):
        """The exact case that shipped broken: trailer at the end of the body."""
        self.record(commit('c' * 40,
                           'fix(devlog): every deploy path records its release (#888)',
                           SQUASH_BODY))
        self.assertIsNotNone(
            self.item.live_at,
            'the Dev-Item trailer sat in the commit body and was ignored, so the '
            'request never showed as finished — the whole point of the log')
        self.assertEqual(self.item.status, DevItem.Status.LIVE)

    def test_a_trailer_buried_mid_line_links_nothing(self):
        """"Dev-Item:" in the middle of a line is prose, not a trailer.

        This started life asserting the opposite — that a mid-subject mention
        should link, "so old behaviour does not regress". Review showed that IS
        the bug: a first-match-wins search over unanchored text linked a commit
        to a backtick in a sentence about trailers. A key is only a key on its
        own line, and a wrong link invents finished work nobody did.
        """
        self.record(commit('d' * 40, 'fix: thing Dev-Item: cfo-2026-09-11-buildlog-finished'))
        self.assertIsNone(self.item.live_at)

    def test_a_commit_with_no_trailer_links_nothing(self):
        """No word matching. A wrong link invents finished work nobody did."""
        self.record(commit('e' * 40, 'fix: build log something something',
                           'mentions the build log but claims no item'))
        self.assertIsNone(self.item.live_at)

    def test_a_trailer_naming_an_unknown_item_is_ignored(self):
        self.record(commit('f' * 40, 'fix: thing', 'Dev-Item: cfo-1999-01-01-does-not-exist'))
        self.assertIsNone(self.item.live_at)

    def test_multiline_bodies_do_not_run_into_each_other(self):
        """Records are split on \\x1e precisely because bodies contain newlines.
        Splitting on newlines would turn one commit into many malformed rows."""
        log = (commit('1' * 40, 'first (#1)', 'a body\nwith lines\n\nDev-Item: cfo-1999-01-01-nope')
               + commit('2' * 40, 'second (#2)', SQUASH_BODY))
        out = self.record(log)
        self.assertIn('2 commit(s)', out, f'expected exactly two commits, got: {out}')
        self.assertIsNotNone(self.item.live_at)

    def test_prose_mentioning_the_trailer_does_not_steal_the_link(self):
        """A PR body that TALKS about trailers — like the one that fixed this —
        writes "Dev-Item:" mid-sentence. A first-match-wins search linked the
        commit to a backtick and the real request stayed un-live: the third
        consecutive near-miss on this one outcome."""
        body = ('This change explains that `Dev-Item:` is a TRAILER, so git puts\n'
                'it at the end of the message rather than in the subject.\n'
                '\n'
                'Dev-Item: cfo-2026-09-11-buildlog-finished\n'
                '\n'
                'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>')
        self.record(commit('7' * 40, 'fix(devlog): read the trailer from the body (#889)', body))
        self.assertIsNotNone(
            self.item.live_at,
            'the prose mention was taken as the trailer, so the real key was '
            'never read and the request never showed as finished')

    def test_a_commit_already_recorded_without_an_item_is_adopted(self):
        """The commit was recorded while the trailer was still invisible, so it
        sits here with no item. Re-running over that range must adopt it —
        otherwise get_or_create returns made=False and the request stays
        stranded for ever."""
        from devlog.models import DevCommit, DevDeploy
        earlier = DevDeploy.objects.create(sha='0' * 40, prev_sha='',
                                           deployed_at=timezone.now(), commit_count=1, ok=True)
        DevCommit.objects.create(sha='8' * 40, author='Someone', subject='fix: thing (#888)',
                                 committed_at=timezone.now(), deploy=earlier, item=None)
        self.record(commit('8' * 40, 'fix: thing (#888)',
                           'Dev-Item: cfo-2026-09-11-buildlog-finished'), sha='5' * 40)
        self.assertIsNotNone(
            self.item.live_at,
            'the commit was already on record with no item and was skipped, so '
            'the request could never go live')
        self.assertEqual(DevCommit.objects.get(sha='8' * 40).item_id, self.item.id)

    def test_one_commit_can_close_two_requests(self):
        second = DevItem.objects.create(client_key='cfo-2026-09-11-second',
                                        asked_text='another ask', title='second',
                                        asked_at=timezone.now())
        self.record(commit('6' * 40, 'fix: two things (#890)',
                           'Dev-Item: cfo-2026-09-11-buildlog-finished\n'
                           'Dev-Item: cfo-2026-09-11-second'))
        second.refresh_from_db()
        self.assertIsNotNone(self.item.live_at)
        self.assertIsNotNone(second.live_at, 'the second named request never went live')

    def test_the_log_can_come_from_stdin(self):
        """Real callers pipe the log in, because argv is capped at 128KB and
        commit bodies now push past that within weeks."""
        import io
        from django.core.management import call_command as cc
        log = commit('4' * 40, 'fix: thing (#891)',
                     'Dev-Item: cfo-2026-09-11-buildlog-finished')
        real, sys.stdin = sys.stdin, io.StringIO(log)
        try:
            cc('devlog_deploy', sha='3' * 40, prev='b' * 40, log_stdin=True, stdout=StringIO())
        finally:
            sys.stdin = real
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.live_at)

    def test_the_old_newline_format_is_still_parsed(self):
        """A caller that has not been updated yet must still record its commits.

        The old format carried subjects only, so it could never have linked a
        trailer anyway — what must not break is the PARSING, not the linking.
        """
        ts = int(timezone.now().timestamp())
        old = (f'{"9" * 40}{UNIT}Someone{UNIT}{ts}{UNIT}fix: one thing\n'
               f'{"a" * 39}0{UNIT}Someone{UNIT}{ts}{UNIT}fix: another thing')
        out = self.record(old)
        self.assertIn('2 commit(s)', out, f'old-format log was not parsed: {out}')
