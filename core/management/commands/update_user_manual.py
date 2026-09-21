"""
update_user_manual — nightly "What's New" builder for the Omni User Manual.

Runs from cron at Botswana midnight (22:00 UTC). The host pipes recent git
commits in (the backend container has no .git), one per line:

    <sha>\x1f<YYYY-MM-DD>\x1f<subject>

For every new `feat:` commit we haven't seen, this turns the commit into a
plain-English "what you can now do" note (via the local reasoning chain —
Ollama/Gemini/DeepSeek, never Anthropic-first) and stores it. The /help page
reads these live, so the manual grows every night with zero redeploy.

Idempotent: deduped by commit sha, so an overlapping commit window (we pass
`--since "8 days ago"`) never double-lists a feature — and a missed night
self-heals on the next run.

Safe on AI outage: if the reasoning chain is unreachable, we still record the
feature using a cleaned-up version of the commit subject.
"""

import json
import re
import sys
import datetime as dt

from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone

from core import ai_assist
from core.manual_models import ManualFeatureEntry, ManualUpdateRun

# Conventional-commit types that are user-facing by default.
FEATURE_TYPES = ('feat', 'feature')
FIX_TYPES     = ('fix', 'perf')

# scope → friendly area label (best-effort; free text also accepted)
AREA_LABELS = {
    'hris': 'HRIS', 'payroll': 'Payroll', 'health': 'Health Care',
    'healthcare': 'Health Care', 'hc': 'Health Care',
    'payments': 'Payments', 'payment': 'Payments', 'banking': 'Banking',
    'bank': 'Banking', 'fnb': 'Banking', 'realpay': 'Banking',
    'procurement': 'Procurement', 'po': 'Procurement',
    'accounting': 'Accounting', 'ledger': 'Accounting', 'je': 'Accounting',
    'coa': 'Accounting', 'reports': 'Reports', 'reporting': 'Reports',
    'assets': 'Assets', 'vendors': 'Vendors', 'billing': 'Vendors',
    'compliance': 'Compliance', 'claims': 'Claims',
    'reinsurance': 'Reinsurance', 'treaty': 'Reinsurance',
    'health-quotes': 'Health Care', 'fx': 'Accounting',
}

_SUBJECT_RE = re.compile(r'^(?P<type>\w+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<msg>.+)$')

SYSTEM_PROMPT = (
    "You write release notes for non-technical staff at an insurance company "
    "who use an internal system called Omni. You are given ONE git commit "
    "subject line. Reply with a compact JSON object and nothing else:\n"
    '{"title": "...", "summary": "...", "area": "..."}\n'
    "Rules:\n"
    "- title: at most 8 words, plain English, describe what a user can now do. "
    "No tech words (never say endpoint, migration, refactor, backend, API, "
    "schema, commit, deploy).\n"
    "- summary: one or two short, friendly sentences — what changed and, if "
    "obvious, where in Omni to find it. Simple words a 13-year-old understands.\n"
    "- area: the best single label from: HRIS, Payroll, Health Care, Payments, "
    "Banking, Procurement, Accounting, Reports, Assets, Vendors, Compliance, "
    "Claims, Reinsurance, General.\n"
    "Return only the JSON."
)


def _parse_subject(subject: str):
    """(type, scope, message) from a conventional-commit subject; type='' if not."""
    m = _SUBJECT_RE.match(subject.strip())
    if not m:
        return '', '', subject.strip()
    return (m.group('type').lower(), (m.group('scope') or '').lower().strip(),
            m.group('msg').strip())


def _humanize(message: str, scope: str) -> str:
    """Fallback title when the AI is unavailable — clean the commit message."""
    msg = message.strip().rstrip('.')
    # drop trailing parenthetical attributions like "(Unami 2026-07-01)"
    msg = re.sub(r'\s*\([^)]*\d{4}[^)]*\)\s*$', '', msg).strip()
    if msg:
        msg = msg[0].upper() + msg[1:]
    return msg or (scope.title() if scope else 'Update')


def _area_for(scope: str) -> str:
    if not scope:
        return 'General'
    key = scope.split(',')[0].split('/')[0].strip().lower()
    return AREA_LABELS.get(key, key.title() if key else 'General')


class Command(BaseCommand):
    help = "Append new feat: commits to the /help User Manual 'What's New' log."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse + summarise, print, store nothing.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Max new commits to add this run (0 = all).')
        parser.add_argument('--include-fixes', action='store_true',
                            help='Also log fix:/perf: commits, not just feat:.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        types = set(FEATURE_TYPES) | (set(FIX_TYPES) if opts['include_fixes'] else set())

        raw = sys.stdin.read() if not sys.stdin.isatty() else ''
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        if not lines:
            self.stdout.write("[manual] no commits on stdin — nothing to do. "
                              "(Pipe: git log --pretty=format:'%H%x1f%cs%x1f%s')")
            return

        self.stdout.write(f"[manual] {len(lines)} commit line(s) in"
                          f"{' (dry-run)' if dry else ''}")

        added = 0
        last_sha = ''
        for ln in lines:
            parts = ln.split('\x1f')
            if len(parts) < 3:
                continue
            sha, date_s, subject = parts[0].strip(), parts[1].strip(), parts[2].strip()
            last_sha = last_sha or sha
            ctype, scope, message = _parse_subject(subject)
            if ctype not in types:
                continue
            if ManualFeatureEntry.objects.filter(commit_sha=sha).exists():
                continue
            if opts['limit'] and added >= opts['limit']:
                self.stdout.write(f"[manual] hit --limit {opts['limit']}; stopping")
                break

            described = self._describe(subject, message, scope)
            if described is None:            # subject flagged PII — never store it
                self.stdout.write(f"  skip {sha[:8]} (subject flagged, not stored)")
                continue
            title, summary, area = described
            try:
                commit_date = dt.date.fromisoformat(date_s)
            except ValueError:
                commit_date = timezone.localdate()

            if dry:
                self.stdout.write(f"  [dry] {sha[:8]} [{area}] {title}")
                added += 1
                continue

            try:
                ManualFeatureEntry.objects.create(
                    commit_sha=sha, commit_date=commit_date, title=title[:200],
                    summary=summary[:1000], area=area[:60], raw_subject=subject[:300],
                    published=True,
                )
            except IntegrityError:
                # A concurrent/overlapping run inserted this sha between our
                # exists() check and here — idempotent, just skip it.
                continue
            added += 1
            self.stdout.write(f"  + {sha[:8]} [{area}] {title}")

        if not dry:
            today = timezone.localdate()
            ManualUpdateRun.objects.update_or_create(
                run_date=today,
                defaults={'commits_seen': len(lines), 'entries_added': added,
                          'last_commit_sha': last_sha},
            )
        self.stdout.write(f"[manual] done — {added} new entry(ies) "
                          f"{'would be ' if dry else ''}added")

    # --- summarisation ------------------------------------------------------
    def _describe(self, subject: str, message: str, scope: str):
        """(title, summary, area) — AI plain-English if available, else humanized.
        Returns None if the subject is flagged as carrying PII (skip; never store)."""
        fallback = (_humanize(message, scope), '', _area_for(scope))
        rep = ai_assist.is_safe_for_ai(subject)
        if not rep.safe:
            return None      # do not persist a PII-bearing commit subject
        try:
            raw = ai_assist.reasoning_complete(
                rep.redacted_text, system_prompt=SYSTEM_PROMPT,
                response_format='json_object', timeout=30.0)
            d = json.loads(raw)
            title = str(d.get('title', '') or '').strip()
            summary = str(d.get('summary', '') or '').strip()
            area = str(d.get('area', '') or '').strip() or _area_for(scope)
            if not title:
                return fallback
            return title, summary, area
        except Exception as exc:                     # noqa: BLE001 — never fail the run
            self.stderr.write(f"[manual] AI unavailable for {subject[:50]!r}: {exc}")
            return fallback
