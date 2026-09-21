"""
run_job — the gate every switchable scheduled job runs through.

  manage.py run_job <name> -- <the real command and its args>

It looks up the switch. Off -> it records a skipped run and exits 0 (a skip is
not a failure). On -> it runs the real command, timing it and keeping the last
2 KB of output.

FAIL OPEN. If the switch lookup itself errors — a bad migration, the table
missing — the job RUNS. A broken dashboard must never be the thing that stops
the backups. That is the whole reason the nine-day outage was so damaging, and
this is the guard against repeating it from the other direction.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

TAIL = 2000


class Command(BaseCommand):
    help = 'Run a management command through the CFO on/off switch.'

    def add_arguments(self, parser):
        parser.add_argument('name')
        parser.add_argument('rest', nargs='*')   # after the `--`, the real command

    def handle(self, *a, **o):
        name = o['name']
        rest = o['rest']
        if not rest:
            self.stderr.write('run_job: nothing to run after the job name')
            return

        enabled, protected, job = self._lookup(name)

        if not enabled and not protected:
            self._record(job, 'skipped', None, switch_on=False)
            self.stdout.write(f'run_job: {name} is switched off — skipped')
            return

        inner, inner_args = rest[0], rest[1:]
        started = timezone.now()
        buf = io.StringIO()
        code, status = 0, 'ok'
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                call_command(inner, *inner_args)
        except SystemExit as e:            # a command that sys.exits
            code = int(e.code or 0)
            status = 'ok' if code == 0 else 'failed'
        except Exception as e:             # noqa: BLE001 — record then re-raise
            code, status = 1, 'failed'
            buf.write(f'\n{type(e).__name__}: {e}')
            self._record(job, status, code, switch_on=enabled, out=buf.getvalue(),
                         started=started)
            self.stdout.write(buf.getvalue()[-TAIL:])
            raise
        self._record(job, status, code, switch_on=enabled, out=buf.getvalue(),
                     started=started)
        self.stdout.write(buf.getvalue()[-TAIL:])

    def _lookup(self, name):
        """(enabled, protected, job_or_None). Fails OPEN: on any error, run."""
        try:
            from jobs.models import ScheduledJob
            from jobs.protected import is_protected
            job = ScheduledJob.objects.filter(name=name).first()
            prot = is_protected(name)
            if job is None:
                return True, prot, None      # unknown job → run, do not block
            return (job.is_enabled or prot), prot, job
        except Exception:                    # noqa: BLE001 — dashboard down ≠ job down
            return True, False, None

    def _record(self, job, status, code, *, switch_on, out='', started=None):
        if job is None:
            return
        try:
            from jobs.models import JobRun
            JobRun.objects.create(
                job=job, started_at=started or timezone.now(),
                ended_at=timezone.now(), status=status, exit_code=code,
                switch_was_on=switch_on, output_tail=(out or '')[-TAIL:])
        except Exception:                    # noqa: BLE001 — never let logging kill the run
            pass
