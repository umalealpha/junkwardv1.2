"""
devlog_deploy — record a release, from inside the deploy script itself.

This is the only writer of "finished". A skill can claim it built something; a
deploy is the only thing that can prove it reached prod, so `live_at` is
stamped here and nowhere else.

Called by infra/host/record-release.sh, which EVERY deploy path runs — the
blue/green script, the Windows SSM path and the deploy workflow. (It is not
called directly by any one deploy script: doing that is how two of the three
paths ended up recording nothing at all.)

  log=$(git log -n 500 --format=$'%H\\x1f%an\\x1f%ct\\x1f%s\\x1f%b\\x1e' "$OLD..$NEW")
  printf '%s' "$log" | docker compose exec -T backend \\
      python manage.py devlog_deploy --sha "$NEW" --prev "$OLD" --log-stdin

Two things about that format are load-bearing:

  * `%b` — the BODY. `Dev-Item:` is a trailer, so git writes it at the END of
    the message, never in the subject. Sending only `%s` (as this did until
    11-Sep-2026) meant the trailer was invisible and NOTHING could ever flip to
    live, however correctly the release itself was recorded.
  * `\\x1e` between records, not a newline — because a body contains newlines.
  * stdin, not `--log` — a single argv argument is capped at 128KB on Linux and
    bodies blow past that within a few dozen commits, deterministically, so no
    retry could recover.

Commits are attached to a build item ONLY by an explicit `Dev-Item: <key>`
trailer, ANCHORED to the start of a line. Matching on words was considered and
rejected: a wrong link invents a finished piece of work nobody did, which is
worse than the honest "shipped, no request recorded" band. The anchoring is the
same principle one step finer — an unanchored search let a PR body that merely
DISCUSSED trailers hijack the link with a mid-sentence mention.
"""
from __future__ import annotations

import datetime as dt
import re
import sys

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

UNIT = '\x1f'
RECORD = '\x1e'   # separates commits, because a commit body contains newlines

# One key per line, anchored: prose that merely mentions "Dev-Item:" mid-sentence
# is not a trailer and must not link anything.
TRAILER = re.compile(r'^Dev-Item:\s*(\S+)\s*$', re.M)


class Command(BaseCommand):
    help = 'Record a deploy and its commits in the CFO build log.'

    def add_arguments(self, parser):
        parser.add_argument('--sha', required=True)
        parser.add_argument(
            '--log-stdin', action='store_true',
            help='read the commit log from stdin instead of --log. A single argv '
                 'argument is capped at 128KB on Linux, and with commit BODIES '
                 'included a few dozen squash merges can exceed it — which fails '
                 'deterministically, so no retry can recover and the release is '
                 'never recorded. Prefer this for any real caller.')
        parser.add_argument('--prev', default='')
        parser.add_argument('--log', default='',
                            help='git log --format=%H\\x1f%an\\x1f%ct\\x1f%s prev..new')
        parser.add_argument('--note', default='')
        parser.add_argument('--failed', action='store_true')

    def handle(self, *a, **o):
        from devlog.models import DevCommit, DevDeploy, DevItem

        # Records are separated by \x1e (RS) so a commit BODY — which contains
        # newlines — can be sent. Older callers sent subject-only records
        # separated by newlines; both are accepted.
        raw = sys.stdin.read() if o['log_stdin'] else (o['log'] or '')
        records = raw.split(RECORD) if RECORD in raw else raw.splitlines()

        rows = []
        for line in records:
            line = line.strip('\r\n')
            parts = line.split(UNIT)
            if len(parts) < 4:
                continue
            sha, author, ts = parts[0], parts[1], parts[2]
            subject = parts[3]
            # Everything after the subject is the body; a trailer lives there.
            body = UNIT.join(parts[4:])
            try:
                when = dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc)
            except (TypeError, ValueError):
                when = timezone.now()
            rows.append((sha.strip(), author.strip(), when, subject.strip(), body))

        with transaction.atomic():
            deploy, created = DevDeploy.objects.get_or_create(
                sha=o['sha'].strip(),
                defaults={'prev_sha': (o['prev'] or '').strip(),
                          'deployed_at': timezone.now(),
                          'commit_count': len(rows),
                          'ok': not o['failed'],
                          'note': (o['note'] or '')[:200]})
            if not created:
                self.stdout.write(f'{deploy.sha[:8]} already recorded — nothing to do.')
                return

            linked = 0
            for sha, author, when, subject, body in rows:
                # `Dev-Item: <key>` is a TRAILER: it sits at the END of the message,
                # never in the subject — searching only the subject (as this did
                # until 11-Sep-2026) could never find one, so nothing ever flipped
                # to live however well the deploy recorded itself.
                #
                # Anchored to the start of a line, one key per line: a PR body that
                # DISCUSSES trailers (this fix's own body does) writes "Dev-Item:"
                # mid-sentence, and a first-match-wins search happily linked the
                # commit to a backtick. Git's own %(trailers) is not usable here —
                # it returns empty whenever a blank line separates the trailer from
                # the Co-Authored-By block, which is how these commits are written.
                message = f'{subject}\n{body}'
                items = [it for it in (
                    DevItem.objects.filter(client_key=k).first()
                    for k in TRAILER.findall(message)) if it is not None]
                item = items[0] if items else None

                c, made = DevCommit.objects.get_or_create(
                    sha=sha, defaults={'author': author[:120], 'subject': subject[:300],
                                       'committed_at': when, 'deploy': deploy,
                                       'item': item})
                # A commit recorded BEFORE the trailer was readable is already here
                # with no item. The only way that happens is that the link was
                # invisible at the time, so adopt it now rather than leave the CFO's
                # request stranded — this self-heals if the marker is ever rewound.
                if not made and c.item_id is None and item is not None:
                    c.item = item
                    c.save(update_fields=['item', 'updated_at'])

                if item is not None:
                    linked += 1
                    # A commit may legitimately close more than one request.
                    # DevCommit.item is a single FK, so the commit links to the
                    # first, but every named request goes live.
                    for it in items:
                        if it.status != DevItem.Status.LIVE:
                            it.status = DevItem.Status.LIVE
                            it.live_at = timezone.now()
                            it.deploy = deploy
                            it.save(update_fields=['status', 'live_at', 'deploy',
                                                   'updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'deploy {deploy.sha[:8]} recorded · {len(rows)} commit(s) · '
            f'{linked} linked to a build item · '
            f'{len(rows) - linked} with no request recorded'))
