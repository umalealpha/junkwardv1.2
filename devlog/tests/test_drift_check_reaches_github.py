"""The release-drift check must be able to reach GitHub when cron runs it.

THE BUG THIS EXISTS TO CATCH is not "the fetch line was deleted". It is "the
fetch runs as a user that holds no key", which is what actually happened and
which reading the script does not reveal:

  10-Sep  a merged fix stayed unshipped for hours and nothing said so.
          check-release-drift.sh shipped to make that gap visible — on cron,
          hourly, weekdays, reviewed, installed.
  11-Sep  its entire log was one line: `drift: cannot fetch origin`. The remote
          is SSH, the deploy key belongs to `ubuntu`, and cron runs as root,
          which has no key. The alarm meant to catch "merged but never live"
          had never once compared production to main in its whole life — while
          the Telegram fix it should have caught sat unmerged for a day.

So this test does not read the script. It RUNS it the way cron does, with git,
sudo, curl and python3 replaced by recorders, and fails unless the fetch was
handed to a user that actually holds the key. An edit that quietly goes back to
fetching as root fails here on the day it lands.
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'infra' / 'host' / 'check-release-drift.sh'

LIVE = 'a' * 40      # the commit production says it is serving
HEAD = 'b' * 40      # what origin/main is


def _stub(path: Path, body: str) -> None:
    path.write_text('#!/usr/bin/env bash\n' + textwrap.dedent(body), encoding='utf-8')
    path.chmod(0o755)


# POSIX only: the stubs are put in front of the script by prepending to PATH,
# and on Windows PATH is ';'-separated while the bash that runs the script
# splits on ':' — the stubs would simply never be found and the test would fail
# for a reason that has nothing to do with the script. CI is Linux.
@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'POSIX + bash only')
class DriftCheckReachesGitHub(SimpleTestCase):
    def _run(self):
        """Run the real script with the world stubbed out, as cron sees it."""
        tmp = Path(tempfile.mkdtemp())
        log = tmp / 'git-calls.log'

        # Be root, and have an `ubuntu` account to drop to — the prod shape.
        _stub(tmp / 'id', '''
            case "$*" in
              -un)      echo root ;;
              -u\ ubuntu) echo 1000 ;;
              -u)       echo 0 ;;
              *)        echo 0 ;;
            esac
        ''')
        # Production answers the health probe with the commit it is serving.
        _stub(tmp / 'curl', f'echo \'{{"commit": "{LIVE}"}}\'\n')
        _stub(tmp / 'python3', f'cat >/dev/null; echo {LIVE}\n')
        # Every git call records whether it arrived through sudo. The marker is
        # set by the sudo stub, so a fetch that skipped sudo stays visible even
        # though the stub goes on to run the very same command.
        _stub(tmp / 'git', f'''
            echo "via_sudo=${{VIA_SUDO:-0}} git $*" >> {log}
            case "$*" in
              *rev-parse*) echo {HEAD} ;;
              *rev-list*)  echo 1 ;;
              *"log "*)    echo 1600000000 ;;
            esac
            exit 0
        ''')
        _stub(tmp / 'sudo', '''
            [ "$1" = "-u" ] && { export SUDO_ASKED_FOR="$2"; shift 2; }
            VIA_SUDO=1 exec "$@"
        ''')

        env = {**os.environ,
               'PATH': f'{tmp}{os.pathsep}{os.environ["PATH"]}',
               'DRIFT_BASE': 'http://127.0.0.1:8000',
               'DRIFT_REPO': str(tmp),
               'GRACE_MINUTES': '0'}
        env.pop('TELEGRAM_BOT_TOKEN', None)
        env.pop('TELEGRAM_CHAT_ID', None)

        proc = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=60)
        return proc, (log.read_text(encoding='utf-8') if log.exists() else '')

    def test_the_fetch_is_handed_to_the_user_that_holds_the_key(self):
        _proc, calls = self._run()
        fetches = [ln for ln in calls.splitlines() if ' fetch ' in ln]
        self.assertTrue(fetches, 'the check never even tried to fetch')
        for line in fetches:
            self.assertTrue(
                line.startswith('via_sudo=1 '),
                'the fetch ran as root, which holds no deploy key — this is the '
                f'"cannot fetch origin" outage all over again: {line!r}')

    def test_it_names_the_drift_instead_of_giving_up(self):
        proc, _calls = self._run()
        self.assertNotIn('cannot fetch origin', proc.stdout)
        self.assertIn('BEHIND main', proc.stdout)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
