"""Every way of building the backend image must stamp which commit it is.

THE BUG THIS EXISTS TO CATCH is not "the build arg was deleted". It is "somebody
built the image by a route that never stamped it" — which is what happened, and
which looks like nothing at all from the outside.

  The health endpoint reports OMNI_BUILD_SHA so a stale production can be SEEN
  rather than assumed current. check-release-drift.sh reads exactly that, and
  refuses to call production current when it cannot tell.

  Measured on prod 11-Sep: the endpoint answered {"commit": "unknown"}. So the
  drift check exited at its very first gate, every run, for its whole life —
  on top of separately being unable to fetch. Two independent faults, one
  silent alarm, and a merged Telegram fix nobody was told about for a day.

  The cause was the familiar one: three ways to deploy, and only
  infra/host/deploy-zero-downtime.sh passed --build-arg GIT_SHA. The CI
  workflow and the Windows SSM path each built their own image, unstamped.

So this does not check a known list of callers. It goes looking for anything
that builds the backend image and fails if that build does not stamp it. A
fourth build path added next month fails here on the day it lands.

The Windows path lives outside this repo (~/.claude/skills/fabe/scripts/
deploy_ssm.py) and cannot be asserted from CI — the same honour-system gap the
release-recording test documents, for the same reason.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]

# A command that builds the backend image, however it is spelled.
BUILDS_BACKEND = re.compile(r'docker\s+compose\b[^\n]*\bbuild\b[^\n]*\bbackend\b'
                            r'|\bbuild\b[^\n]*--build-arg[^\n]*\bbackend\b')
STAMPS = re.compile(r'--build-arg\s+GIT_SHA')

# Where a deploy could plausibly live. Everything else is documentation.
SEARCH = ('infra', '.github', 'ops')
SUFFIXES = {'.sh', '.yml', '.yaml', '.py'}


def _candidates():
    for top in SEARCH:
        base = ROOT / top
        if not base.exists():
            continue
        for p in base.rglob('*'):
            if p.suffix in SUFFIXES and p.is_file():
                yield p


class EveryBackendBuildIsStamped(SimpleTestCase):
    def test_we_actually_found_the_build_paths(self):
        """A matcher that finds nothing would pass the real test silently."""
        found = [p for p in _candidates()
                 if BUILDS_BACKEND.search(p.read_text(encoding='utf-8', errors='ignore'))]
        self.assertTrue(
            found,
            'no file appears to build the backend image — the pattern has gone '
            'stale and the guard below is now asserting nothing')

    def test_no_build_path_produces_an_image_that_cannot_name_itself(self):
        unstamped = []
        for p in _candidates():
            text = p.read_text(encoding='utf-8', errors='ignore')
            for line_no, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith('#'):
                    continue
                if not BUILDS_BACKEND.search(line):
                    continue
                # A build may be split across continued lines; look at the
                # whole statement, not the one line the match landed on.
                window = '\n'.join(text.splitlines()[max(0, line_no - 4):line_no + 4])
                if not STAMPS.search(window):
                    unstamped.append(f'{p.relative_to(ROOT)}:{line_no}')
        self.assertEqual(
            unstamped, [],
            'these build the backend image without --build-arg GIT_SHA, so it '
            'reports its commit as "unknown" and the drift alarm goes blind: '
            f'{unstamped}')
