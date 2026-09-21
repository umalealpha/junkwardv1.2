"""Every way of deploying must record the release (CFO 2026-09-11).

THE BUG THIS EXISTS TO CATCH IS NOT "the call was deleted". It is "somebody
added a SECOND way to deploy that does not record", which is what actually
happened and what the existing guard tests could not see:

  9-Sep  devlog_deploy shipped; its docstring claimed the deploy called it.
         Nothing did. "Finished today" could never fill.
  #780   wired it into infra/host/deploy-zero-downtime.sh and added tests
         asserting that call exists. Those tests went green.
  11-Sep the band was STILL empty, because two other deploy paths existed:
         the Windows SSM path (the CFO's own machine) and .github/workflows/
         deploy.yml, each doing its own git reset + compose build + up. A test
         that watches one caller cannot see a caller it was never told about.

So this test does not check a known list of callers. It goes looking for
anything that behaves like a deploy — a file that brings containers up with
`docker compose ... up` — and fails if any of them does not also record the
release. A fourth deploy path added next month fails here on the day it lands.

The Windows path lives outside this repo (~/.claude/skills/fabe/scripts/
deploy_ssm.py) and so cannot be asserted from CI. It is covered by the same
shared script; that one is on the honour system, which is exactly why the
shared script exists instead of three copies of the logic.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
RECORDER_NAME = 'record-release.sh'

# A file that brings containers up. Deliberately NOT a single-line match: the
# blue/green script builds its compose call in an array (`DC=(docker compose …)`
# then `"${DC[@]}" up -d`), so a one-line `docker compose … up` pattern silently
# missed the very script this whole fix started from — caught by the
# "we actually found the deploy paths" test below, which is why it is there.
USES_COMPOSE = re.compile(r'docker\s+compose\b')
BRINGS_UP = re.compile(r'\bup\s+-d\b')

# Where a deploy could plausibly live. Kept narrow on purpose: a test that
# scanned the whole repo would trip over docs and examples.
SEARCH = [
    (ROOT / 'infra' / 'host', '*.sh'),
    (ROOT / '.github' / 'workflows', '*.yml'),
]

# Files that legitimately bring containers up without being a release:
# setup/first-install, local test harnesses, and the recorder itself.
NOT_A_DEPLOY = {
    'setup-zero-downtime.sh',
    'test-disk-guard.sh',
    'test-return-home.sh',
    'caddy-ride-out-restarts.sh',
    'alpha-finance-backup.sh',
    'ci.yml',
    RECORDER_NAME,
}


def code_only(text):
    """Drop comment lines.

    Mentioning a script in a comment is not calling it. The first version of this
    test excused .github/workflows/deploy.yml because a COMMENT there named
    deploy-zero-downtime.sh — so the test passed while the workflow recorded
    nothing, which is precisely the failure it was written to prevent. Caught by
    reverting the fix and watching it stay green.
    """
    return '\n'.join(ln for ln in text.splitlines() if not ln.lstrip().startswith('#'))


# Delegation has to be an invocation — `bash …/deploy-zero-downtime.sh` — not a
# mention. Same reason as above.
# The path may be built inline — the blue/green script calls the recorder as
# `bash "$(dirname "${BASH_SOURCE[0]}")/record-release.sh"` — so allow anything
# between the interpreter and the script name, on the same line. Comment lines
# are already stripped, so this cannot be satisfied by a mention.
INVOKES_BLUEGREEN = re.compile(r'(bash|sh|\./|source)\s+[^\n]*deploy-zero-downtime\.sh')
INVOKES_RECORDER = re.compile(r'(bash|sh|\./|source)\s+[^\n]*'
                              + RECORDER_NAME.replace('.', r'\.'))


def deploy_paths():
    for folder, pattern in SEARCH:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob(pattern)):
            if path.name in NOT_A_DEPLOY:
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            if USES_COMPOSE.search(text) and BRINGS_UP.search(text):
                yield path, text


class EveryDeployPathRecordsTests(SimpleTestCase):
    def test_we_actually_found_the_deploy_paths(self):
        """A scan that silently matches nothing would pass for ever and prove
        nothing — the failure mode of the tests this one replaces."""
        found = [p.name for p, _ in deploy_paths()]
        self.assertGreaterEqual(
            len(found), 2,
            f'expected to find at least the blue/green script and the deploy '
            f'workflow; found {found}. If deploys moved, point this test at them.')

    def test_each_one_records_the_release(self):
        missing = []
        for path, text in deploy_paths():
            code = code_only(text)
            direct = bool(INVOKES_RECORDER.search(code))
            # A path that hands off to another deploy script inherits its recording.
            delegates = (path.name != 'deploy-zero-downtime.sh'
                         and INVOKES_BLUEGREEN.search(code))
            if not (direct or delegates):
                missing.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            missing, [],
            'these bring containers up but never record the release, so the CFO '
            'Build Log\'s "Finished today" band stays empty for anything shipped '
            f'through them: {missing}. Add, after the containers are up:\n'
            '  sudo RR_DC="docker compose --env-file /etc/alpha-finance/.env" '
            'bash infra/host/record-release.sh || true')
