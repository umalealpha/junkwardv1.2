"""Import agent commission workbooks' 'Data' tabs into submission lines.

Dry-run by default (parses + reports counts/totals, writes nothing). Pass
--commit to create the draft submissions. Runs server-side; row values never
leave the box.

  python manage.py import_commission_workbooks "<file-or-folder>" \
      --group bdu --period 2026-06 [--commit]
"""
import glob
import os
import re

from django.core.management.base import BaseCommand, CommandError

from commissions.importer import import_workbook


class Command(BaseCommand):
    help = "Import commission workbook 'Data' tabs into submission lines (dry-run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument('path', help='A workbook file, or a folder of them.')
        parser.add_argument('--group', default='independent',
                            help='Group key for new agents: independent | in_house | bdu (ignored with --inhouse)')
        parser.add_argument('--period', required=True, help='Month as YYYY-MM')
        parser.add_argument('--commit', action='store_true', help='Actually write (default: dry-run)')
        parser.add_argument('--submit', action='store_true',
                            help='Push each loaded submission into the review chain (→ 1st review)')
        parser.add_argument('--as-user', default='',
                            help='Email or username of the person doing the submit — REQUIRED with --submit '
                                 '(a submission must record who submitted it; no NULL submitters — b6ccaa38)')
        parser.add_argument('--inhouse', action='store_true',
                            help='Treat the file(s) as in-house GROSS summaries (one submission per agent row)')

    def handle(self, *args, **o):
        if not re.match(r'^\d{4}-(0[1-9]|1[0-2])$', o['period']):
            raise CommandError(f"--period must be YYYY-MM (got {o['period']!r}).")
        submit_user = None
        if o.get('submit'):
            ident = (o.get('as_user') or '').strip()
            if not ident:
                raise CommandError(
                    "--submit requires --as-user <email|username>: a submission must record who "
                    "submitted it (no NULL submitters — b6ccaa38).")
            from django.contrib.auth import get_user_model
            from django.db.models import Q
            U = get_user_model()
            # Resolve unambiguously: a value that matches more than one user (an
            # email of one, a username of another) is REFUSED — the submitter on a
            # money record must be certain, never a best guess.
            matches = list(U.objects.filter(Q(email__iexact=ident) | Q(username__iexact=ident))[:2])
            if not matches:
                raise CommandError(f"--as-user {ident!r} does not match any user.")
            if len(matches) > 1:
                raise CommandError(
                    f"--as-user {ident!r} matches more than one user — pass the exact username.")
            submit_user = matches[0]
        path = o['path']
        if os.path.isfile(path):
            files = [path]
        else:
            files = sorted(glob.glob(os.path.join(path, '**', '*.xls*'), recursive=True))
        files = [f for f in files
                 if '__MACOSX' not in f and not os.path.basename(f).startswith(('~', '.'))]
        if not files:
            self.stdout.write('No workbook files found.')
            return
        total_lines = 0
        for f in files:
            try:
                if o['inhouse']:
                    from commissions.importer import import_inhouse
                    r = import_inhouse(f, o['period'], commit=o['commit'], submit=o['submit'], user=submit_user)
                    tag = 'OK ' if o['commit'] else 'DRY'
                    self.stdout.write(f"{tag} in-house {os.path.basename(f)[:30]}: "
                                      f"{r['agents']} agents, {r.get('created', 'gross ' + str(r.get('gross')))}")
                    continue
                r = import_workbook(f, o['group'], o['period'], commit=o['commit'], submit=o['submit'], user=submit_user)
                total_lines += r['lines']
                tag = 'OK ' if o['commit'] else 'DRY'
                self.stdout.write(f"{tag} {r['agent']}: {r['lines']} lines · gross {r.get('gross')} · {r.get('status','(dry)')}")
            except Exception as e:
                self.stdout.write(f"ERR {os.path.basename(f)}: {type(e).__name__}: {e}")
        self.stdout.write(f"— {len(files)} file(s), {total_lines} line(s) total"
                          f"{'' if o['commit'] else ' (dry-run, nothing written)'}")
