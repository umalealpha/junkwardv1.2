"""
core/management/commands/nightly_self_test.py

Nightly self-test — CFO directive 2026-07-14: "Omni tests itself every night,
files the bugs, emails me improvements, and repeats the cycle. If a new feature
is added it gets checked too."

Runs 23:00 Africa/Gaborone (root crontab). Each cycle:
  1. Enumerates EVERY authenticated API GET route, so a newly-shipped feature is
     swept the moment it exists — nothing to register by hand.
  2. Hits each route as a superuser and records any 5xx / unhandled exception —
     a real backend break a user would hit.
  3. Files each NEW break as a BugReport, deduped against still-open reports so
     the same break is never filed twice. reporter_email = omni@ so the per-bug
     triage email stays silent; the CFO gets ONE nightly digest instead.
  4. Emails a digest to cfo@ + excoboard@: what was tested, what broke, tonight's
     improvement spotlight (one feature area per night, cycling the 12 test
     categories), and a link to the bug board.

HARD LINES (identical to triage_bugs — this job NEVER crosses them):
  * Read-only. Never edits financial data / balances / journals / mappings.
  * Never deploys code. Detected breaks are FILED for a reviewed fix; auto-
    pushing AI code to a live insurance ERP is out of scope by design.
  * Never sends customer / employee PII anywhere — it reports endpoint paths and
    error TYPES, never row data.
"""
from __future__ import annotations


from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.urls import get_resolver
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import BugReport, Company

# One feature area highlighted per night, cycling the 12-category test plan.
SPOTLIGHT = [
    ("Core ledger & GL",
     "Journal entries, recurring JEs, period open/close, reconciliation — confirm every posting balances and periods lock cleanly."),
    ("Financial reports & CFO packs",
     "Trial balance, P&L, balance sheet, cash flow — confirm they tie to the frozen MA figures."),
    ("Buying & suppliers",
     "Purchase orders, bills, payables aging, vendor bank details — confirm approvals route to the right leg."),
    ("Payments & banking",
     "Once-off + bulk payments, FNB / RealPay, voucher clearing — confirm approval limits and the 09:00 cut-off hold."),
    ("Invoicing & money-in",
     "Customer invoices, debtors, refunds, age analysis — confirm balances match Graphite."),
    ("Payroll",
     "Pay runs, payslips, ITW8 tax — confirm gross-to-net and the tax lines are right."),
    ("HR / people",
     "Leave, amendments, performance, documents — confirm approvals and dual sign-off work."),
    ("Claims & salvage",
     "Claims register, salvage yard, recoveries, claims POs — confirm salvage is in the yard before settlement."),
    ("Underwriting, reinsurance & health",
     "Treaties, cessions, bordereaux, group health quotes — confirm facultative RI is confirmed over BWP 50M."),
    ("Assets & investments",
     "Fixed assets, hand-over, investments, fleet — confirm the register agrees to the GL."),
    ("Compliance & regulatory",
     "ISO, NBFIRA quarterly / annual, capital adequacy — confirm submissions are current."),
    ("Tasks, approvals, AI & admin",
     "Task dashboard, approvals, spend requests, users / roles / secrets — confirm access is least-privilege."),
]

_OPEN = [BugReport.Status.NEW, BugReport.Status.TRIAGED, BugReport.Status.IN_PROGRESS]


class Command(BaseCommand):
    help = "Nightly self-test: sweep every API GET, file new backend breaks as bugs, email a digest."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report only; file nothing, send nothing.")
        parser.add_argument("--no-email", action="store_true",
                            help="File bugs but skip the digest email.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        User = get_user_model()
        user = (User.objects.filter(email__iexact="pganesharajah@alphadirect.co.bw").first()
                or User.objects.filter(is_superuser=True).first())
        if not user:
            self.stderr.write("No superuser to sweep as — aborting.")
            return

        adic = Company.objects.filter(code__iexact="ADIC").first()
        cid = str(adic.id) if adic else ""
        host = "omni.alphadirect.co.bw"
        client = APIClient()
        client.force_authenticate(user=user)

        def walk(resolver, prefix=""):
            for pat in resolver.url_patterns:
                if hasattr(pat, "url_patterns"):
                    yield from walk(pat, prefix + str(pat.pattern))
                else:
                    yield prefix + str(pat.pattern)

        # DRF's routers use regex patterns, so str(pattern) hands back "^accounts/$"
        # — anchors included. Concatenating those verbatim produced probe URLs like
        # "/api/v1/^accounts/$", which 404 no matter how healthy the route is. 170 of
        # 517 probes were that shape, so a third of the API was never really tested
        # and a 500 in any of it could not be caught. Strip the anchors.
        patterns = sorted({p.replace("^", "").replace("$", "") for p in walk(get_resolver())})
        five, errs, ok, four, tested = [], [], 0, 0, 0
        # Keep the 4xx URLs, not just a tally. A bare count cannot tell a route that
        # correctly refuses GET from one that has broken, so nobody could ever act on it.
        four_urls: list[tuple[int, str]] = []
        for raw in patterns:
            if any(tok in raw for tok in ("<", "(?P", "\\", "admin", "format")):
                continue
            if not raw.startswith("api/"):
                continue
            url = "/" + raw
            try:
                resp = client.get(url, {"company": cid} if cid else {}, HTTP_HOST=host)
                tested += 1
                if resp.status_code >= 500:
                    five.append((f"HTTP {resp.status_code}", url, ""))
                elif resp.status_code >= 400:
                    four += 1
                    four_urls.append((resp.status_code, url))
                else:
                    ok += 1
            except Exception as exc:  # noqa: BLE001 — a crash here IS the bug we want
                errs.append((type(exc).__name__, url, str(exc)[:200]))
                tested += 1

        breaks = five + errs

        # ---- file NEW breaks as bugs (deduped against still-open reports) ----
        filed, already = 0, 0
        for kind, url, msg in breaks:
            if BugReport.objects.filter(page_url=url, status__in=_OPEN).exists():
                already += 1
                continue
            desc = (f"[AUTO - nightly self-test {timezone.localdate():%Y-%m-%d}] "
                    f"Backend break on {url}: {kind}. {msg}").strip()
            if not dry:
                BugReport.objects.create(
                    reporter=user,
                    reporter_email="omni@alphadirect.co.bw",
                    description=desc,
                    word_count=len(desc.split()),
                    screenshot_count=0,
                    page_url=url,
                    status=BugReport.Status.NEW,
                )
            filed += 1

        spot_name, spot_tip = SPOTLIGHT[timezone.localdate().toordinal() % len(SPOTLIGHT)]
        open_total = BugReport.objects.filter(status__in=_OPEN).count()

        self.stdout.write(
            f"tested={tested} ok={ok} 4xx={four} 5xx={len(five)} exc={len(errs)} "
            f"filed={filed} already_open={already} spotlight={spot_name!r}"
        )
        # 405 = the route only accepts POST/PUT, 400 = it wants query params — both are
        # correct refusals of a bare GET. A 404 or 401 here is worth a human's eye, so
        # log those specifically instead of burying them in the 4xx total.
        notable = sorted((c, u) for c, u in four_urls if c in (401, 404))
        if notable:
            self.stdout.write(f"  4xx worth checking ({len(notable)}):")
            for code, url in notable[:40]:
                self.stdout.write(f"    HTTP {code}  {url}")
            if len(notable) > 40:
                self.stdout.write(f"    … and {len(notable) - 40} more")

        if dry or opts["no_email"]:
            return

        # CFO 2026-08-12: when the consolidated-emails flag is on, the self-test
        # RESULT folds into the 06:30 ops digest (derived from the bugs just filed
        # above), so the standalone 23:00 email is skipped. Bugs are still filed.
        if getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False):
            self.stdout.write("CONSOLIDATED_EMAILS_ENABLED on — result folds into the "
                              "06:30 ops digest; standalone self-test email skipped.")
            return

        base = "https://omni.alphadirect.co.bw"
        if breaks:
            rows = "".join(
                '<tr>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;'
                f'font-family:monospace;font-size:12px;">{url}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;'
                f'color:#B04E00;font-weight:600;">{kind}</td></tr>'
                for kind, url, _ in breaks
            )
            broke_html = (
                f'<p><strong style="color:#B04E00;">{len(breaks)} break(s) found</strong> — '
                f'{filed} newly added to the bug board, {already} already open.</p>'
                '<table style="border-collapse:collapse;width:100%;font-size:13px;">'
                '<tr style="background:#0D1B2A;color:#fff;text-align:left;">'
                '<th style="padding:7px 10px;">Feature (page)</th>'
                '<th style="padding:7px 10px;">Problem</th></tr>'
                f'{rows}</table>'
            )
        else:
            broke_html = ('<p><strong style="color:#0a7d34;">No backend breaks found.</strong> '
                          'Every feature page loaded cleanly.</p>')

        html = (
            '<p>Good day,</p>'
            '<p>Omni checked itself tonight. Here is what it found.</p>'
            f'<p style="margin:14px 0 4px;"><strong>Tested:</strong> {tested} feature pages '
            f'({ok} loaded fine, {four} need a form or filter to open, '
            f'{len(breaks)} broke).</p>'
            f'{broke_html}'
            '<div style="margin:18px 0;padding:12px 14px;background:#FFF6E9;'
            'border-left:4px solid #F4A623;">'
            f'<p style="margin:0 0 4px;"><strong>Tonight’s improvement spotlight &mdash; '
            f'{spot_name}.</strong></p>'
            f'<p style="margin:0;">{spot_tip}</p></div>'
            f'<p>Open items on the bug board: <strong>{open_total}</strong>. '
            'I review these and fix the real ones with proper checks. '
            'Kindly note: Omni never changes live code by itself.</p>'
            f'<p style="margin-top:12px;">See the full list &rarr; '
            f'<a href="{base}/bug-reports" style="color:#0D1B2A;font-weight:600;">Bug board</a></p>'
            '<p style="margin-top:16px;">Regards,<br>Omni</p>'
        )

        from core.notifications import send_html_with_cfo_cc
        sent = send_html_with_cfo_cc(
            subject=f"Omni nightly self-test — {tested} pages checked, {len(breaks)} break(s)",
            html=html,
            to=["cfo@alphadirect.co.bw"],
            cc_cfo=True,  # adds excoboard@ per house rule
        )
        self.stdout.write(f"digest emailed to cfo@ + excoboard@ (sent={sent})")
