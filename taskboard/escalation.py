"""Same-day CFO ageing escalation for payment requests.

CFO 2026-09-14, in his words: payment requests that sit too long must
escalate to him the SAME DAY, not wait for a next-day digest.

REUSES rather than parallels what already existed:
  * the ageing threshold is the SAME "stale" definition the 09:30
    payment_daily_digest already uses (taskboard.payment_digest.STALE_DAYS) —
    nothing new was invented, it is now just Finance-editable (below);
  * the row table and card layout are payment_digest's own
    ``_rows_table`` / house HTML, so this email and the daily digest can
    never disagree about what a row looks like;
  * who it goes to is reporting.models.ReportRecipient.route_for(), the SAME
    mechanism the three-way check / advance-mismatch / weekly-claims reports
    already use, so Finance edit the list on one screen for every report,
    this one included — falling back to the CFO's own address (matching
    payment_daily_digest's own default) when nobody has configured it yet.

Deliberately different from the daily digest in one way: NO one-tap decision
links. This can go out mid-day, to a recipient list Finance may edit, and a
personal Approve link is only as personal as its delivery (Fable 2026-09-13)
— a route with more than one address, or one Finance later adds someone to,
must never carry a link that decides as the CFO. Read-only, escalation only.

    python manage.py payment_ageing_escalation
    python manage.py payment_ageing_escalation --dry-run
    python manage.py payment_ageing_escalation --stale-days 2
    python manage.py payment_ageing_escalation --to me@x.com
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

log = logging.getLogger(__name__)

#: Matches send_advance_mismatch_report / send_failed_debits_report / the
#: three-way check — one place Finance edit the recipient list, per report.
REPORT_SLUG = 'payment-ageing-escalation'

#: reporting.models.ReportRecipient falls back to this when nobody has set
#: the list up yet — same default payment_daily_digest itself uses, because
#: the point of this job is that the CFO hears about it even on day one.
_DEFAULT_TO = ['pganesharajah@alphadirect.co.bw']

SETTING_KEY = 'payment_escalation.stale_days'

#: The threshold already used elsewhere as "waiting on the CFO too long" —
#: taskboard.payment_digest.STALE_DAYS. Only the SEED value for the
#: Finance-editable setting; once the row exists Finance's edit wins.
DEFAULT_STALE_DAYS = 3


def stale_days() -> int:
    """The ageing threshold, editable by Finance in the Omni admin under
    Taskboard > Taskboard settings — no deploy needed (CFO 2026-09-14)."""
    from .models import TaskboardSetting
    row, _ = TaskboardSetting.objects.get_or_create(
        key=SETTING_KEY,
        defaults={
            'value': str(DEFAULT_STALE_DAYS),
            'description': (
                'Days a payment request may sit waiting on the CFO before it '
                'is escalated to him the same day, rather than waiting for '
                'the 09:30 payment digest.'),
        })
    try:
        n = int(row.value)
        if n < 0:
            raise ValueError(row.value)
        return n
    except (TypeError, ValueError):
        log.warning('payment escalation: setting %r holds %r, not a whole '
                    'number — using the default %d', SETTING_KEY, row.value,
                    DEFAULT_STALE_DAYS)
        return DEFAULT_STALE_DAYS


def candidates(threshold_days: int | None = None, now=None) -> tuple[list[dict], int]:
    """Every PENDING_CFO request old enough to escalate that has not already
    been escalated. Returns (rows, threshold_used) — plain data, no HTML, no
    model calls, so this and its tests read exactly the same thing."""
    from .models import PaymentRequest

    now = now or timezone.localtime()
    today = now.date()
    threshold = stale_days() if threshold_days is None else threshold_days

    qs = (PaymentRequest.objects
          .filter(status=PaymentRequest.Status.PENDING_CFO,
                  escalated_at__isnull=True)
          .select_related('created_by'))

    rows = []
    for pr in qs:
        # created_at is UTC-aware; .date() on it is the UTC date, while `today`
        # above is the Gaborone date. Between 22:00 and midnight Gaborone those
        # differ, so every request raised in that window aged a day too old and
        # escalated to the CFO early (and CI went red in the same window with the
        # product right and the test wrong - third recurrence of this class,
        # MACHINE-TALK 12-Sep). Localise before taking the date.
        age = (today - timezone.localtime(pr.created_at).date()).days
        if age < threshold:
            continue
        rows.append({
            'ref': pr.ref, 'entity': pr.entity or '—',
            'payee': pr.payee or pr.subject or '—',
            'currency': pr.currency, 'total': Decimal(str(pr.total or 0)),
            'age_days': age,
            'loader': pr.inputter or (pr.created_by.get_full_name()
                                      if pr.created_by else '—'),
            'task_id': pr.task_id, 'id': pr.id,
        })
    rows.sort(key=lambda r: -r['age_days'])
    return rows, threshold


def build_html(rows: list[dict], threshold: int) -> str:
    # _rows_table lives on the management command, not taskboard.payment_digest
    # (which only holds collect()/narrative() — the row FIGURES, not the HTML).
    from .management.commands.payment_daily_digest import _rows_table

    try:
        from core.notifications import no_reply_banner
        banner = no_reply_banner()
    except Exception:                                    # noqa: BLE001
        banner = ''

    n = len(rows)
    total_by_currency = {}
    for r in rows:
        total_by_currency[r['currency']] = total_by_currency.get(
            r['currency'], Decimal('0')) + r['total']
    totals_line = ' · '.join(
        f'{k} {v:,.2f}' for k, v in sorted(total_by_currency.items())) or '—'

    h = (
        banner +
        '<div style="margin:0;background:#F3F4F6;font-family:\'Book Antiqua\','
        'Palatino,Georgia,serif;color:#1F2937;padding:20px 12px;">'
        '<div style="max-width:720px;margin:0 auto;background:#fff;border-radius:14px;'
        'overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">'
        '<div style="background:#B91C1C;padding:20px 26px;">'
        '<div style="font-size:12px;color:#FEE2E2;letter-spacing:.06em;text-transform:uppercase">'
        'Alpha Direct &middot; Payments &middot; Same-day escalation</div>'
        f'<div style="color:#fff;font-size:19px;font-weight:700;margin-top:3px;">'
        f'{n} payment request{"" if n == 1 else "s"} now over {threshold} '
        f'day{"" if threshold == 1 else "s"} waiting on you</div>'
        f'<div style="font-size:13px;color:#FEE2E2;margin-top:3px;">{totals_line}</div>'
        '</div>'
        '<div style="padding:22px 26px;font-size:15px;line-height:1.6;">'
        f'<p style="margin:0 0 14px;">These crossed {threshold} '
        f'day{"" if threshold == 1 else "s"} old today while still waiting on '
        f'you — you are being told now, not at tomorrow&rsquo;s 09:30 summary. '
        f'Nothing here has changed in Omni; this only reads.</p>'
    )
    h += _rows_table(rows)
    h += (
        '<p style="margin:14px 0 0;font-size:12px;color:#9CA3AF;">Omni never '
        'moves money — you authorise the real payment yourself in FNB, '
        'with two-factor.</p>'
        '<p style="margin:14px 0 0;">Regards,<br><b>Omni</b><br>'
        '<span style="color:#6B7280;">Alpha Direct</span></p></div></div></div>'
    )
    return h


def subject(rows: list[dict], threshold: int) -> str:
    n = len(rows)
    return (f'\U0001F534 {n} payment request{"" if n == 1 else "s"} over '
            f'{threshold} day{"" if threshold == 1 else "s"} — waiting on you')


def recipients() -> tuple[list[str], list[str]]:
    """(to, cc) — Finance's own list via ReportRecipient, or the CFO
    himself when nobody has set one up yet."""
    from reporting.models import ReportRecipient
    route = ReportRecipient.route_for(REPORT_SLUG)
    if route:
        return route['to'], route['cc']
    return list(_DEFAULT_TO), []


def escalate(*, threshold_days: int | None = None, to_override: list[str] | None = None,
            dry_run: bool = False, now=None) -> dict:
    """Find, email and mark the candidates. Returns a small result dict for
    the command to print. Marking `escalated_at` happens ONLY after the send
    succeeds (or on a dry run, never) — a failed send must be retried the
    next time this runs, not silently treated as done."""
    rows, threshold = candidates(threshold_days, now=now)
    if not rows:
        return {'count': 0, 'sent': 0, 'to': [], 'threshold': threshold}

    to = to_override or recipients()[0]
    cc = [] if to_override else recipients()[1]
    if not to:
        log.warning('payment escalation: %d row(s) to escalate but no recipients '
                    'configured — nothing sent', len(rows))
        return {'count': len(rows), 'sent': 0, 'to': [], 'threshold': threshold}

    html = build_html(rows, threshold)
    subj = subject(rows, threshold)

    if dry_run:
        return {'count': len(rows), 'sent': 0, 'to': to, 'cc': cc,
                'threshold': threshold, 'dry_run': True, 'subject': subj}

    from core.notifications import send_html_with_cfo_cc
    sent = send_html_with_cfo_cc(
        subject=subj, html=html, to=to, cc=cc or None,
        text_fallback=(f'{len(rows)} payment request(s) are now over {threshold} '
                       f'day(s) waiting on you. Open '
                       f'https://omni.alphadirect.co.bw/payment-requests'),
    )

    # Mark AFTER the send succeeds, so a send that raises OR reports nothing
    # sent leaves every row eligible again on the next run rather than silently
    # going quiet. send_html_with_cfo_cc returns the delivered count: a 0 means
    # the CFO was never told, and marking on 0 would bury those rows for good
    # (candidates() only ever picks up escalated_at IS NULL).
    from .models import PaymentRequest
    now_ts = now or timezone.localtime()
    if sent:
        PaymentRequest.objects.filter(id__in=[r['id'] for r in rows]).update(
            escalated_at=now_ts)
    else:
        log.warning('payment escalation: send reported 0 delivered — leaving '
                    '%d row(s) un-escalated to retry on the next run', len(rows))

    return {'count': len(rows), 'sent': sent, 'to': to, 'cc': cc,
            'threshold': threshold, 'subject': subj}
