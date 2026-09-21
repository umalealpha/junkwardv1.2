"""infra/timedoctor-watch.sh — hourly Time Doctor access watch (CFO 19-Sep-2026).

Drives the host script with stub commands and checks it speaks only when access
changes: blocked -> email + Telegram once; working again -> Telegram once; an
unclear probe changes nothing.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

SCRIPT = Path(__file__).resolve().parent.parent / 'infra' / 'timedoctor-watch.sh'
DENIED = 'Time Doctor rejected the live probe: 403 denied'
OK = 'OK — token carries no readable exp claim but the live probe succeeded.'


class TimeDoctorWatchTest(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, 'state')
        self.calls = os.path.join(self.dir, 'calls')

    def run_watch(self, probe_output, deliver=True):
        probe = os.path.join(self.dir, 'probe.txt')
        Path(probe).write_text(probe_output)
        stub = os.path.join(self.dir, 'stub.sh')
        said = 'alert emailed to 3 recipient(s) [x]: send()=1. / telegram sent' if deliver else 'send failed'
        Path(stub).write_text(f'echo "$1" >> {self.calls}\necho "{said}"\n')
        env = dict(os.environ,
                   TDW_STATE=self.state,
                   TDW_PROBE=f'cat {probe}',
                   TDW_ALERT=f'bash {stub} email',
                   TDW_TELEGRAM=f'bash {stub} telegram')
        subprocess.run(['bash', str(SCRIPT)], env=env, check=True, capture_output=True, text=True)
        return Path(self.calls).read_text().split() if os.path.exists(self.calls) else []

    def test_blocked_alerts_email_and_telegram_once(self):
        self.assertEqual(self.run_watch(DENIED), ['email', 'telegram'])
        self.assertEqual(self.run_watch(DENIED), ['email', 'telegram'])  # second hour: silent
        self.assertEqual(Path(self.state).read_text().strip(), 'down')

    def test_recovery_sends_telegram_only(self):
        self.run_watch(DENIED)
        self.assertEqual(self.run_watch(OK), ['email', 'telegram', 'telegram'])
        self.assertEqual(Path(self.state).read_text().strip(), 'ok')

    def test_healthy_stays_silent(self):
        self.assertEqual(self.run_watch(OK), [])

    def test_unclear_probe_changes_nothing(self):
        self.run_watch(DENIED)
        self.assertEqual(self.run_watch('Error response from daemon: container not running'),
                         ['email', 'telegram'])
        self.assertEqual(Path(self.state).read_text().strip(), 'down')

    def test_failed_delivery_retries_next_hour(self):
        self.assertEqual(self.run_watch(DENIED, deliver=False), ['email', 'telegram'])
        self.assertFalse(os.path.exists(self.state))  # not remembered as alerted
        self.assertEqual(self.run_watch(DENIED), ['email', 'telegram', 'email', 'telegram'])
        self.assertEqual(Path(self.state).read_text().strip(), 'down')

    def test_damaged_state_file_still_alerts_an_outage(self):
        Path(self.state).write_text('')
        self.assertEqual(self.run_watch(DENIED), ['email', 'telegram'])
