"""Task 2 — scheduled sends for the six Finance monitoring reports.

Pack Task 2. Weekly (Monday) for A1, A3 and B1; monthly for B3. The email carries
COUNTS, TOTALS, the dates it covers and a LINK into the screen — NEVER a customer
list (pack §3.6). A dead replica, or any other failure, sends an error notice and
exits non-zero — never a silent "all clear" (pack §3.7).

Reuses:
  * reporting.finance_monitoring.REPORTS — the same six builders the screen uses.
  * core.notifications.send_html_with_cfo_cc — house sender; ccs excoboard@ and
    hard-blocks admin@ via _NEVER_CC.

  manage.py send_finance_monitoring --frequency weekly [--dry-run]
  manage.py send_finance_monitoring --slug failed-debits --dry-run
"""
from __future__ import annotations

import datetime as _dt
import inspect
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from reporting.finance_monitoring import REPORTS, ReplicaUnavailable
from django.utils import timezone

log = logging.getLogger(__name__)

# Pack Task 2 → which reports go out at each frequency. B2 (policy status
# integrity) is the same builder as A1, so it rides on the weekly A1 send rather
# than a second fortnightly job over identical data.
#
# failed-debits is DAILY, not weekly: Keetile asked for it "as soon as the
# transaction fails". RealPay only reports on a daily batch (build_failed_debits
# docstring), so daily — the morning after that batch lands — is the earliest
# honest cadence; a per-transaction alert is not possible from a daily source.
# payment-status is the fortnightly Payment status reader (pack #4).
FREQUENCY_SLUGS = {
    "daily": ["failed-debits"],
    "weekly": ["policy-status-integrity", "collections-vs-graphite"],
    "fortnightly": ["payment-status"],
    "monthly": ["contract-expiry"],
}

# The window a scheduled build must cover, sized to the cadence — never the
# builder's interactive default (build_failed_debits defaults to ONE day, so a
# weekly email built bare would print a confident near-zero every Monday).
FREQUENCY_WINDOW_DAYS = {"daily": 1, "weekly": 7, "fortnightly": 14, "monthly": 31}

CADENCE_WORD = {"daily": "today", "weekly": "this week",
                "fortnightly": "this fortnight", "monthly": "this month"}

# Per-report recipient routing (Keetile Mokhendo, UniCoin debtors automation,
# 2026-09-07). A report listed here goes to its named `to` + `cc` instead of the
# registry `owner`; excoboard@ is still auto-CC'd by the house sender, and admin@
# stays hard-blocked. Bakang's address is his own address on that live thread.
# Reports NOT listed keep the default owner routing.
#   failed-debits  (A3, plain-English failure reasons) — LIVE, daily.
#   payment-status (#4, Payment status reader)          — LIVE, fortnightly.
#   Perpetual debit catcher (A2) is NOT a live report yet (no plan-size source),
#   so it gets no entry until it exists — its routing is these same 3 people.
_UNICOIN_DEBTORS = {
    "to": ["lmababa@alphadirect.co.bw", "bmhusiwa@insurance.co.bw"],
    "cc": ["kmokhendo@alphadirect.co.bw"],
}
REPORT_RECIPIENTS = {
    "failed-debits": _UNICOIN_DEBTORS,
    "payment-status": _UNICOIN_DEBTORS,
}


def _route_for(slug: str):
    """Who this report goes to: the list Finance keep on screen, else the
    hardcoded default above.

    CFO 2026-09-11 — Finance must be able to change a distribution list without
    a developer. The fallback is deliberate: a report with no rows yet keeps
    delivering exactly as it does today, so turning this on could not silently
    stop a report that is working.
    """
    from reporting.models import ReportRecipient
    try:
        chosen = ReportRecipient.route_for(slug)
    except Exception:  # noqa: BLE001
        # Reading the list is not worth losing the report over. If the lookup
        # fails for any reason, fall back to the built-in list and say so —
        # a monitoring report that silently stops arriving is exactly the
        # failure this whole pack exists to prevent.
        log.exception("could not read the recipient list for %s", slug)
        chosen = None
    return chosen or REPORT_RECIPIENTS.get(slug)


def _call_builder(builder, frequency):
    """Run a builder with a date window sized to the cadence, when it takes one.

    Builders that accept date_from/date_to (collections, failed debits) get the
    full cadence window; those that do not (policy status integrity) run as-is.
    """
    accepted = set(inspect.signature(builder).parameters)
    kwargs = {}
    days = FREQUENCY_WINDOW_DAYS.get(frequency or "")
    if days and {"date_from", "date_to"} <= accepted:
        today = timezone.localdate()
        kwargs = {"date_from": today - _dt.timedelta(days=days), "date_to": today}
    return builder(**kwargs)


def _summary_rows_html(summary: dict) -> str:
    """The summary dict as plain label/number rows. Numbers only — a non-numeric
    value is skipped, so a future builder cannot leak a string into the email."""
    out = []
    for key, val in summary.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        label = key.replace("_", " ").capitalize()
        if isinstance(val, float):
            shown = (f"P {val:,.2f}"
                     if any(t in key for t in ("amount", "risk", "bwp")) else f"{val:,.2f}")
        else:
            shown = f"{val:,}"
        out.append(
            f'<tr><td style="padding:4px 12px 4px 0;color:#475569;">{label}</td>'
            f'<td style="padding:4px 0;font-weight:600;color:#0D1B2A;text-align:right;">{shown}</td></tr>'
        )
    return "".join(out)


def _covered_line(meta: dict) -> str:
    frm, to = (meta or {}).get("date_from"), (meta or {}).get("date_to")
    if frm and to:
        return f'<p style="margin:0 0 10px;color:#64748B;font-size:13px;">Covering {frm} to {to}.</p>'
    return ""


def _email_html(*, title, cadence_word, summary_html, link,
                covered="", unavailable=False) -> str:
    if unavailable:
        body = (
            '<p style="margin:0 0 12px;color:#B91C1C;"><b>This report could not run.</b> '
            'Treat this as "not checked", not as a clean result. It will retry on the next '
            'run; tell IT if it persists.</p>'
        )
    else:
        body = (
            f'<p style="margin:0 0 4px;">Here is where <b>{title}</b> stands {cadence_word}. '
            'Open the screen for the policy-level detail — it is not in this email.</p>'
            f'{covered}'
            f'<table style="border-collapse:collapse;margin:0 0 16px;">{summary_html}</table>'
        )
    return (
        '<div style="font-family:\'Segoe UI\',Arial,sans-serif;color:#1F2937;">'
        f'<div style="background:#0D1B2A;padding:16px 22px;border-radius:10px 10px 0 0;">'
        f'<span style="color:#F4A623;font-size:16px;font-weight:700;">{title}</span></div>'
        f'<div style="padding:18px 22px;border:1px solid #E2E8F0;border-top:none;border-radius:0 0 10px 10px;">'
        f'{body}'
        f'<p style="margin:8px 0 0;"><a href="{link}" '
        'style="background:#F4A623;color:#0D1B2A;text-decoration:none;padding:8px 16px;'
        'border-radius:6px;font-weight:600;">Open the report</a></p>'
        '</div></div>'
    )


class Command(BaseCommand):
    help = "Send the scheduled Finance monitoring report summaries (counts + link, no customer data)."

    def add_arguments(self, parser):
        parser.add_argument("--frequency", choices=sorted(FREQUENCY_SLUGS), default=None)
        parser.add_argument("--slug", default=None, help="send one report by slug")
        parser.add_argument("--dry-run", action="store_true",
                            help="build and print the email, send nothing")

    def handle(self, *args, **opts):
        if opts["slug"]:
            slugs = [opts["slug"]]
        elif opts["frequency"]:
            slugs = FREQUENCY_SLUGS[opts["frequency"]]
        else:
            raise CommandError("Pass --frequency weekly|monthly or --slug <slug>.")
        frequency = opts.get("frequency")
        cadence_word = CADENCE_WORD.get(frequency or "", "now")
        base = getattr(settings, "PUBLIC_BASE_URL", "https://omni.alphadirect.co.bw")
        link = f"{base}/banking/realpay/monitoring"

        failed, sent_ok = [], 0
        for slug in slugs:
            cfg = REPORTS.get(slug)
            if not cfg:
                failed.append(slug)
                self.stderr.write(f"unknown slug {slug!r}, skipping")
                continue
            title, owner = cfg["title"], cfg["owner"]
            route = _route_for(slug)
            to_list = route["to"] if route else [owner]
            cc_list = route.get("cc") if route else None
            unavailable = False
            try:
                result = _call_builder(cfg["builder"], frequency)
                html = _email_html(title=title, cadence_word=cadence_word,
                                   summary_html=_summary_rows_html(result["summary"]),
                                   link=link, covered=_covered_line(result.get("meta", {})))
                subject = f"Finance monitoring — {title}"
            except ReplicaUnavailable:
                unavailable = True
            except Exception:  # noqa: BLE001 — never let one report kill the rest, and never send nothing silently
                log.exception("finance monitoring send failed for %s", slug)
                unavailable = True
            if unavailable:
                html = _email_html(title=title, cadence_word=cadence_word,
                                   summary_html="", link=link, unavailable=True)
                subject = f"Finance monitoring — {title} (could not run)"
                failed.append(slug)

            if opts["dry_run"]:
                cc_note = f" cc {cc_list}" if cc_list else ""
                self.stdout.write(f"\n=== DRY RUN: {subject} -> {to_list}{cc_note} ===")
                self.stdout.write(html)
                if not unavailable:
                    sent_ok += 1
                continue

            try:
                from core.notifications import send_html_with_cfo_cc
                delivered = send_html_with_cfo_cc(
                    subject=subject, html=html, to=to_list, cc=cc_list,
                    text_fallback=f"{title}: open {link}",
                )
                sent_ok += 1
                self.stdout.write(f"sent {slug} -> {to_list} (recipients: {delivered})")
            except Exception:  # noqa: BLE001
                log.exception("finance monitoring email send failed for %s", slug)
                failed.append(slug)

        if failed or sent_ok == 0:
            raise CommandError(
                f"finance monitoring send incomplete: sent={sent_ok}, failed={failed or 'none, but nothing sent'}"
            )
