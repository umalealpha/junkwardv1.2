"""
check_recipient_access — before asking someone to do something in Omni, prove
they can get there (CFO 2026-09-05).

    manage.py check_recipient_access --email a@… b@… --path /sop-bank
    manage.py check_recipient_access --email a@… --body-file mail.html

Prints one JSON object: {"ok": bool, "verdicts": [{email, ok, reason, path}]}.
Exit code 0 when every recipient can reach every page, 2 otherwise. Read-only.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.access_check import check_recipients


class Command(BaseCommand):
    help = "Check that email recipients can reach the Omni page(s) an email points them to."

    def add_arguments(self, parser):
        parser.add_argument("--email", nargs="+", required=True)
        parser.add_argument("--path", nargs="*", default=[])
        parser.add_argument("--body-file", default="")

    def handle(self, *args, **o):
        text = ""
        if o["body_file"]:
            with open(o["body_file"], encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        verdicts = check_recipients(o["email"], text, paths=o["path"] or None)
        ok = all(v.ok for v in verdicts)
        self.stdout.write(
            json.dumps({"ok": ok, "verdicts": [v.as_dict() for v in verdicts]})
        )
        if not ok:
            raise SystemExit(2)
