"""
core/management/commands/triage_bugs.py

Autonomous off-box bug-triage runner — CFO directive 2026-06-12:
"I don't want human, hourly, get it done." Runs hourly on the prod box via
root crontab, independent of any Claude session.

For every BugReport still in NEW status it:
  1. PII-filters the description via core.ai_assist.is_safe_for_ai
     (Anthropic-only policy AD-POL-AI-GOV-001, with the CFO's 2026-05-09
     DeepSeek-reasoning carve-out). If the text carries un-redactable PII it
     is NOT sent to any model — it falls back to a deterministic keyword
     classification.
  2. Classifies (DeepSeek when safe, else keywords) into:
       illegal | data_financial | duplicate | feature | code
  3. Takes the SAFE action and emails the reporter exactly once (the NEW->X
     transition), reusing the same feedback email the triage board sends:
       illegal / out-of-policy -> TRIAGED + declined note (never acted on)
       data / financial        -> TRIAGED + "needs CFO 3x-confirm" note
       duplicate-of-resolved   -> RESOLVED + reference to the prior fix
       feature                 -> IN_PROGRESS + backlog note
       code                    -> IN_PROGRESS + acknowledgement + plan summary

HARD LINES (this runner never crosses them):
  * Never edits financial data / balances / journals / mappings.
  * Never deploys code. Real code fixes are acknowledged + summarised for a
    reviewed change — auto-pushing AI code to a live insurance ERP is out of
    scope by design.
  * Never sends customer / employee PII to any external model.

Idempotent: only NEW reports are touched; once actioned a report leaves NEW,
so it is never re-processed or re-emailed. --dry-run previews; --limit N caps.
"""
from __future__ import annotations

import difflib
import json
import logging
import re

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import ai_assist
from core.models import BugReport

log = logging.getLogger('bug-triage')

# Deterministic safety guards — these OVERRIDE the model. We would rather
# wrongly flag a benign report for human review than auto-action a dangerous
# one. Anchored to imperative phrasing so ordinary descriptions don't trip it.
_ILLEGAL_RX = re.compile(
    r'\b(delete all|drop table|truncate table|'
    r'disable (the )?(auth|authentication|login|security|access ?control)|'
    r'bypass (the )?(approval|auth|maker.?checker|controls?|sign.?off)|'
    r'export (all|every) (customer|client|policy|policyholder|member)|'
    r'dump (the )?(database|db)|'
    r'(reveal|show|print|leak) (the )?(password|secret|api ?key|token)|'
    r'without (approval|sign.?off|authorisation|authorization))\b',
    re.I,
)
_FINANCIAL_RX = re.compile(
    r'\b(currency|exchange rate|\bfx\b|re-?rate|journal entr|posted journal|'
    r'\bJE\b|trial balance|\bGWP\b|\bPAT\b|net earned premium|\bNEP\b|'
    r'gl mapping|account mapping|revenue (mapping|recogni|figure)|'
    r'opening balance|reconcil|balance sheet (figure|total|number))\b',
    re.I,
)

CLASSIFY_SYSTEM = (
    "You are a software-triage assistant for an insurance ERP. Given a bug "
    "report's page URL and description, classify it. Respond ONLY with valid "
    'JSON: {"category":"code|data_financial|feature|illegal",'
    '"severity":"low|medium|high","summary":"<=120 char neutral summary",'
    '"plan":"<=160 char suggested next step"}. '
    "category=illegal ONLY if it asks to bypass security/approvals, expose or "
    "export personal data, delete data, or anything unlawful. "
    "category=data_financial if fixing it means changing accounting/financial "
    "data (currency, journals, balances, mappings, revenue figures). "
    "category=feature if it asks for a new capability that does not exist. "
    "Otherwise category=code."
)

_DUP_THRESHOLD = 0.80   # difflib ratio on same page_url before we auto-close


class Command(BaseCommand):
    help = "Autonomous hourly bug-board triage (classify, act safely, email reporter)."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Classify + print only; change nothing, send no email.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Max NEW reports to process this run (0 = all).')

    # Postgres advisory-lock key for "a bug-triage run is in flight". One lock
    # serialises EVERY caller — the hourly cron, the CFO's 'Run triage now'
    # button (BugTriageRunView), and any manual shell run — across all gunicorn
    # workers/processes. Without it two overlapping runs can both pick up the
    # same NEW report and double-email the reporter.
    LOCK_KEY = 824217

    def handle(self, *args, **opts):
        from django.db import connection

        dry = opts['dry_run']
        with connection.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s)', [self.LOCK_KEY])
            if not cur.fetchone()[0]:
                self.stdout.write('[triage] another triage run is already in '
                                  'progress — skipped')
                return
        try:
            qs = BugReport.objects.filter(status=BugReport.Status.NEW).order_by('created_at')
            if opts['limit']:
                qs = qs[:opts['limit']]
            reports = list(qs)
            self.stdout.write(f"[triage] {len(reports)} NEW report(s)"
                              f"{' (dry-run)' if dry else ''}")
            for r in reports:
                try:
                    self._process(r, dry)
                except Exception:                       # noqa: BLE001
                    log.exception('[triage] failed on %s', r.id)
                    self.stdout.write(f"  {str(r.id)[:8]} ERROR (see bug-triage log)")
            self.stdout.write("[triage] done")
        finally:
            with connection.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(%s)', [self.LOCK_KEY])

    # --------------------------------------------------------------- core
    def _process(self, r: BugReport, dry: bool):
        # Notes below are what the reporter reads (accountants, not engineers):
        # warm, plain English, no jargon. Any technical detail goes to the log,
        # never the email.
        desc = r.description or ''

        # 1. Hard guard — anything that asks to bypass controls / expose data /
        #    delete records goes to a person for sign-off; never acted on here.
        if _ILLEGAL_RX.search(desc):
            return self._act(r, BugReport.Status.TRIAGED, dry,
                "Thanks for sending this through. This one needs a manager to "
                "sign off before we take it any further, so we've passed it "
                "straight on to them. We'll come back to you once we hear back.")

        # 2. PII filter decides whether we may use a reasoning engine at all.
        rep = ai_assist.is_safe_for_ai(desc)
        cat, summary, plan = (None, '', '')
        if rep.safe:
            cat, summary, plan = self._classify(r.page_url or '', rep.redacted_text)
        if summary or plan:                                   # internal only
            log.info('[triage] %s cat=%s :: %s | %s', str(r.id)[:8], cat, summary, plan)

        # 3. Financial guard overrides a soft code/feature call.
        if cat in (None, 'code', 'feature') and _FINANCIAL_RX.search(desc):
            cat = 'data_financial'

        if cat == 'illegal':
            return self._act(r, BugReport.Status.TRIAGED, dry,
                "Thanks for sending this through. This one needs a manager to "
                "sign off before we can take it further, so we've passed it on "
                "to them. We'll be in touch.")

        if cat == 'data_financial':
            return self._act(r, BugReport.Status.TRIAGED, dry,
                "Thanks for flagging this. Because it affects the figures, we're "
                "checking it carefully with the Finance team before anything is "
                "changed — nothing has been touched in the meantime. We'll "
                "let you know what we find.")

        # 4. Already sorted elsewhere?
        if self._find_duplicate(r) is not None:
            return self._act(r, BugReport.Status.RESOLVED, dry,
                "Good news — this was already sorted on our side. Please "
                "give the page a quick refresh (hold Shift and click reload). If "
                "you still see it after that, just reply to this email and we'll "
                "take another look right away.")

        # 5. Couldn't read it safely (looked like it held personal details) —
        #    straight to a person, nothing sent anywhere.
        if not rep.safe:
            return self._act(r, BugReport.Status.TRIAGED, dry,
                "Thanks for this. We've passed it straight to someone on the team "
                "to look into, and we'll be in touch shortly.")

        # 6. Ordinary fix or a new request.
        if cat == 'feature':
            return self._act(r, BugReport.Status.IN_PROGRESS, dry,
                "Thanks for the suggestion — it's a good one. We've added it "
                "to our list to build, and we'll let you know once it's ready "
                "for you.")
        return self._act(r, BugReport.Status.IN_PROGRESS, dry,
            "Thanks for flagging this — we've got it and someone is looking "
            "into it now. We'll let you know as soon as it's sorted.")

    # ------------------------------------------------------------ helpers
    def _classify(self, page_url: str, safe_text: str):
        prompt = f"PAGE: {page_url}\nDESCRIPTION:\n{safe_text}"
        try:
            # DeepSeek first, Gemini as automatic backup (CFO 2026-06-12).
            # 15s per engine: classification is a ~300-word prompt; anything
            # slower is a hung engine. Keeps a multi-report run inside the
            # 120s gunicorn window when fired from the CFO button.
            raw = ai_assist.reasoning_complete(
                prompt, system_prompt=CLASSIFY_SYSTEM,
                response_format='json_object', timeout=15.0)
            d = json.loads(raw)
            return (str(d.get('category', 'code')).lower(),
                    str(d.get('summary', ''))[:160],
                    str(d.get('plan', ''))[:200])
        except ai_assist.DeepSeekUnavailable as exc:
            log.warning('[triage] both reasoning engines unavailable: %s', exc)
            return None, '', ''
        except (ValueError, KeyError, TypeError) as exc:
            log.warning('[triage] classify parse error: %s', exc)
            return None, '', ''

    def _find_duplicate(self, r: BugReport):
        if not r.page_url:
            return None
        cands = (BugReport.objects
                 .filter(status=BugReport.Status.RESOLVED, page_url=r.page_url)
                 .exclude(pk=r.pk).order_by('-resolved_at')[:10])
        a = (r.description or '')[:600]
        for c in cands:
            if difflib.SequenceMatcher(None, a, (c.description or '')[:600]).ratio() >= _DUP_THRESHOLD:
                return c
        return None

    def _act(self, r: BugReport, new_status: str, dry: bool, note: str):
        tag = f"  {str(r.id)[:8]} {r.status}->{new_status}"
        if dry:
            self.stdout.write(f"{tag} [dry] {note[:90]}")
            return
        r.status = new_status
        r.resolution_note = note
        fields = ['status', 'resolution_note', 'updated_at']
        if new_status in (BugReport.Status.RESOLVED, BugReport.Status.WONT_FIX):
            r.resolved_at = timezone.now()
            fields.append('resolved_at')
        r.save(update_fields=fields)
        emailed = self._email(r, new_status)
        self.stdout.write(f"{tag} emailed={emailed} :: {note[:70]}")

    @staticmethod
    def _email(r: BugReport, new_status: str) -> bool:
        if not r.reporter_email:
            return False
        try:
            from core.bug_report_views import BugReportDetailView, _STATUS_LABELS
            from core.notifications import send_html_with_cfo_cc
            send_html_with_cfo_cc(
                subject=f'[Omni] Your bug report is now: '
                        f'{_STATUS_LABELS.get(new_status, new_status)}',
                html=BugReportDetailView._feedback_html(r, new_status),
                to=[r.reporter_email],
            )
            return True
        except Exception:                            # noqa: BLE001
            log.exception('[triage] feedback email failed for %s', r.id)
            return False
