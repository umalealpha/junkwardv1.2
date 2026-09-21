"""watchdog/report.py — build and send the nightly audit email (house template),
idempotent per day.

Sent to the CFO; the shared helper auto-CCs the EXCO inbox per house rule. Internal
mail, so it carries the red do-not-reply banner.
"""
from __future__ import annotations

import logging

from django.utils.html import escape

from watchdog.models import Severity, WatchdogReportLog

log = logging.getLogger("watchdog")

NAVY, ORANGE = "#0D1B2A", "#F4A623"
_CFO = "pganesharajah@alphadirect.co.bw"


def _table(title: str, findings, *, accent: str) -> str:
    if not findings:
        return ""
    rows = []
    for f in findings:
        rows.append(
            f'<tr><td style="padding:7px 10px;border-bottom:1px solid #E2E0DB;">'
            f'<b>{escape(f.title)}</b>'
            f'<div style="font-size:12px;color:#6B7280;">'
            f'{escape(f.detail)}</div></td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #E2E0DB;'
            f'white-space:nowrap;color:#6B7280;">{escape(f.module or "—")}</td>'
            f'<td style="padding:7px 10px;border-bottom:1px solid #E2E0DB;'
            f'color:#6B7280;">{escape(f.auto_action or "—")}</td></tr>')
    return (
        f'<h2 style="color:{NAVY};font-size:16px;border-bottom:1px solid #E2E0DB;'
        f'padding-bottom:8px;margin-top:24px;border-left:4px solid {accent};'
        f'padding-left:8px;">{escape(title)}</h2>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<tr style="background:#F1F0EC;text-align:left;color:#6B7280;">'
        '<th style="padding:6px 10px;">Finding</th>'
        '<th style="padding:6px 10px;">Module</th>'
        '<th style="padding:6px 10px;">Action</th></tr>'
        + "".join(rows) + "</table>")


def build_html(run, findings) -> str:
    danger = [f for f in findings if f.severity == Severity.DANGER]
    warn   = [f for f in findings if f.severity == Severity.WARN]
    info   = [f for f in findings if f.severity == Severity.INFO]

    mode = "report-only (dry run)" if run.dry_run else "auto-fix armed"
    head = (
        f'<div style="border-bottom:3px solid {NAVY};padding-bottom:12px;margin-bottom:20px;">'
        f'<h1 style="color:{NAVY};font-size:20px;margin:0;">🐕 Omni Watchdog — nightly audit</h1>'
        f'<p style="color:#6B7280;font-size:13px;margin:5px 0 0;">'
        f'{run.run_date:%A, %d %B %Y} · focus: <b>{escape(run.focus or "nightly")}</b> · {mode}</p></div>')

    total = run.findings_safe + run.findings_danger
    if total == 0 and not info:
        summary = (f'<p style="font-size:15px;">All {run.checks_run} checks passed. '
                   f'Nothing to report tonight. ✅</p>')
    else:
        summary = (
            '<table style="border-collapse:collapse;font-size:14px;margin-bottom:6px;"><tr>'
            f'<td style="padding:8px 16px;background:#FEF2F2;border-radius:6px;">'
            f'<b style="font-size:20px;color:#DC2626;">{run.findings_danger}</b><br>'
            '<span style="color:#6B7280;font-size:12px;">flagged for you</span></td>'
            '<td style="width:10px;"></td>'
            f'<td style="padding:8px 16px;background:#FFFBEB;border-radius:6px;">'
            f'<b style="font-size:20px;color:#D97706;">{run.findings_safe}</b><br>'
            '<span style="color:#6B7280;font-size:12px;">safe issues</span></td>'
            '<td style="width:10px;"></td>'
            f'<td style="padding:8px 16px;background:#F0FDF4;border-radius:6px;">'
            f'<b style="font-size:20px;color:#16A34A;">{run.checks_passed}/{run.checks_run}</b><br>'
            '<span style="color:#6B7280;font-size:12px;">checks passed</span></td></tr></table>')

    body = head + summary
    body += _table("🔴 Flagged — needs a human (finance / permissions / data)",
                   danger, accent="#DC2626")
    body += _table("🟠 Safe issues — auto-fixable when armed", warn, accent=ORANGE)
    body += _table("ℹ️ Context — bug board & today's changes", info, accent=NAVY)

    body += (
        f'<p style="color:#DC2626;font-size:11px;margin-top:26px;padding:8px 12px;'
        f'background:#FEF2F2;border-radius:4px;">⚠️ Automated system email. '
        f'Do not reply — log any feedback in Omni.</p>')
    return body


def send_report(run, findings, *, force: bool = False) -> bool:
    """Send the nightly email once per day. Returns True if actually sent."""
    from core.notifications import send_html_with_cfo_cc

    if not force:
        _, fresh = WatchdogReportLog.objects.get_or_create(report_date=run.run_date)
        if not fresh:
            log.info("watchdog report already sent for %s", run.run_date)
            return False

    subject = (f"Omni Watchdog — {run.run_date:%d %b %Y} — "
               f"{run.findings_danger} flagged, {run.findings_safe} safe "
               f"[{run.focus or 'nightly'}]")
    html = build_html(run, findings)
    try:
        send_html_with_cfo_cc(
            subject, html, [_CFO],
            text_fallback=(f"Omni Watchdog {run.run_date}: {run.findings_danger} "
                           f"flagged, {run.findings_safe} safe, "
                           f"{run.checks_passed}/{run.checks_run} passed."))
        return True
    except Exception:   # noqa: BLE001 — an email problem must not lose the run
        WatchdogReportLog.objects.filter(report_date=run.run_date).delete()
        log.exception("watchdog report email failed for %s", run.run_date)
        return False
