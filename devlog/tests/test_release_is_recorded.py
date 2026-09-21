"""The deploy must record itself (CFO 2026-09-09, widened 2026-09-11).

`devlog_deploy` is the only writer of `live_at`, and it shipped with a docstring
claiming the deploy script called it. Nothing did — verified by `git grep`, no
caller existed anywhere outside the app. So "Finished today" could never fill
however much was released, and the dashboard looked broken when it was honest.

#780 wired it into infra/host/deploy-zero-downtime.sh, and these tests held that
call in place. THEY STILL PASSED WHILE THE BAND STAYED EMPTY, because they only
ever knew about one deploy path: the Windows seat — the CFO's own machine —
deploys over SSM with its own git reset + compose build + up and never runs that
script at all. A test that guards one caller cannot see a second caller that
does not record.

So the logic now lives in infra/host/record-release.sh and every path calls that
one file. These assert the shared script exists, still passes what devlog_deploy
needs, and is still called by the deploy script in this repo. Delete any of it
and they go red.
"""
from pathlib import Path

from django.test import SimpleTestCase

HOST = Path(__file__).resolve().parents[2] / 'infra' / 'host'
RECORDER = HOST / 'record-release.sh'
DEPLOY = HOST / 'deploy-zero-downtime.sh'


class ReleaseRecorderTests(SimpleTestCase):
    """The one implementation every deploy path shares."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = RECORDER.read_text(encoding='utf-8') if RECORDER.is_file() else ''

    def test_the_recorder_exists_where_every_deploy_path_expects_it(self):
        self.assertTrue(RECORDER.is_file(),
                        f'{RECORDER} is gone — both deploy paths run it by path, '
                        'and neither will notice: it is called with || true')

    def test_it_calls_devlog_deploy(self):
        """The regression that shipped: written, tested, never called."""
        self.assertIn('manage.py devlog_deploy', self.text,
                      'nothing records the release any more — "Finished today" '
                      'will silently stay empty for every future release')

    def test_it_passes_the_sha_it_deployed(self):
        self.assertIn('--sha', self.text)

    def test_it_passes_the_commit_range_so_dev_item_trailers_can_link(self):
        for flag in ('--prev', '--log'):
            self.assertIn(flag, self.text,
                          f'{flag} missing — commits carrying a Dev-Item trailer '
                          'can no longer be attached to the work that asked for them')

    def test_the_previous_sha_is_remembered_between_deploys(self):
        """The caller resets the repo before this runs, so the old sha is already
        gone by then. Without the marker there is no range at all."""
        self.assertIn('DEVLOG_MARKER', self.text)

    def test_recording_never_fails_a_deploy(self):
        """Traffic is already served by then. A bookkeeping row must not make a
        caller believe prod is broken and start rolling back."""
        self.assertIn('exit 0', self.text)

    def test_the_compose_invocation_is_overridable(self):
        """Blue/green needs its extra -f files; the SSM path does not. One script
        serves both only if the caller can say how to reach the container."""
        self.assertIn('RR_DC', self.text)


class DeployScriptStillRecordsTests(SimpleTestCase):
    """The in-repo deploy path must still call the recorder."""

    def test_the_deploy_script_exists_where_the_callers_expect_it(self):
        self.assertTrue(DEPLOY.is_file(), f'{DEPLOY} is gone — callers run it by path')

    def test_the_deploy_calls_the_recorder(self):
        text = DEPLOY.read_text(encoding='utf-8')
        self.assertIn('record-release.sh', text,
                      'the deploy no longer records the release')
        self.assertIn('record_release || true', text,
                      'the call must not be able to fail the deploy')
