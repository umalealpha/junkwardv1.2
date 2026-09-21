"""
Regression test for the "setting read but never plumbed through compose" bug
class (found 2026-07-30 while verifying ADRisk's Alpha Brain 8/16 status).

``settings.py`` read ``INTEL_SUMMARY_TOKEN`` via ``config()``, and
``core.intel_summary.token_ok`` fails CLOSED when it is empty — the correct,
safe design. But ``docker-compose.yml`` enumerates the backend's environment
key by key, and the three ``INTEL_SUMMARY_*`` keys were never added to it. So
in prod the token was always ``''`` and GET /api/v1/intel/summary/ returned 401
to every caller, including Alpha Brain holding the right token. The feed looked
"configured" (the token existed in SSM /graphite/OMNI_INTEL_TOKEN) yet could
never authenticate — a silent half-delivery, invisible from either side.

A token that gates an endpoint is worthless if the container can't receive it,
so each one below must appear in the backend service's environment block.
Add to ``GATING_ENV_KEYS`` whenever a new shared-secret setting is introduced.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = REPO_ROOT / 'docker-compose.yml'

# Env keys that gate authentication, authorisation, or reachability of a live
# integration. Both directions of the Omni <-> Alpha Brain link belong here:
# the inbound feed token, and the outbound URL/token used to read the Brain.
GATING_ENV_KEYS = (
    'INTEL_SUMMARY_TOKEN',
    'INTEL_SUMMARY_ALLOWED_IPS',
    'INTEL_SUMMARY_COMPANY',
    'ALPHA_BRAIN_SUMMARY_URL',
    'ALPHA_BRAIN_TOKEN',
)


def _backend_environment_block() -> str:
    """The text of the backend service's `environment:` mapping."""
    text = COMPOSE.read_text()
    backend = re.search(r'^  backend:\n(.*?)(?=^  \S)', text, re.S | re.M)
    assert backend, 'backend service not found in docker-compose.yml'
    env = re.search(r'^    environment:\n(.*?)(?=^    \S)', backend.group(1), re.S | re.M)
    assert env, 'backend service has no environment block'
    return env.group(1)


class IntelTokenWiringTests(SimpleTestCase):
    def test_gating_env_keys_are_passed_to_the_backend_container(self):
        block = _backend_environment_block()
        missing = [k for k in GATING_ENV_KEYS if f'{k}:' not in block]
        self.assertEqual(
            missing, [],
            'These settings gate a live endpoint but are not passed through to '
            'the backend container, so prod can never set them and the '
            'fail-closed check rejects every caller: ' + ', '.join(missing),
        )

    def test_something_actually_reads_each_gating_key(self):
        """Guards the mirror case: plumbed through compose but never read.

        Two legitimate read styles in this repo: ``config('KEY')`` in
        settings.py, and ``os.environ.get('KEY')`` at the point of use (how
        core/compliance_brain.py reads the Brain URL). Either counts.
        """
        sources = [(REPO_ROOT / 'alpha_finance' / 'settings.py').read_text()]
        sources += [p.read_text() for p in (REPO_ROOT / 'core').glob('*.py')]
        blob = '\n'.join(sources)
        for key in GATING_ENV_KEYS:
            read = f"config('{key}'" in blob or f"environ.get('{key}'" in blob
            self.assertTrue(
                read,
                f'{key} is wired into docker-compose.yml but nothing reads it, '
                f'so setting it in .env would do nothing.',
            )

    def test_token_check_fails_closed_when_token_is_unset(self):
        """The safety property that made the miswiring silent rather than loud."""
        from core.intel_summary import token_ok

        class _Req:
            headers = {'Authorization': 'Bearer whatever-the-caller-sends'}

        with self.settings(INTEL_SUMMARY_TOKEN=''):
            self.assertFalse(token_ok(_Req()))
        with self.settings(INTEL_SUMMARY_TOKEN='the-real-token'):
            self.assertFalse(token_ok(_Req()))
            _Req.headers = {'Authorization': 'Bearer the-real-token'}
            self.assertTrue(token_ok(_Req()))
