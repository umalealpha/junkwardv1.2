"""
fnb/health_watch.py

Watch the FNB link and email the CFO + Finance leads when it goes DOWN (and
again when it RECOVERS). CFO directive 2026-08-04 — Prathap, Kago, Pako want to
be told the moment the bank connection stops working, not to discover it later.

"Down" is deliberately narrow so it does NOT cry wolf over the known, ongoing
notifications-scope (403) issue or the one account rejected on statements (400):

    DOWN  = in the last 90 min there were zero SUCCESSFUL FNB calls AND at least
            3 hard failures, where a hard failure is a timeout / no-response /
            HTTP 5xx (i.e. FNB not answering) — NOT a 4xx client/entitlement code.

The watcher only emails on a state CHANGE (up->down, down->up). While down it
re-reminds at most once every 6 hours. The very first run just records a
baseline and stays silent, so installing it never blasts a surprise email.

The alert body is written in plain English by omni's PII-firewalled reasoning
cascade (DeepSeek first). It only ever sees the same masked signals as the page.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from django.db.models.functions import Coalesce

from core.ai_assist import is_safe_for_ai, reasoning_complete, DeepSeekUnavailable
from core.notifications import send_with_cfo_cc
from .ai_health import collect_safe_signals, _signals_to_text
from .models import FNBBatchSubmission, FNBSyncLog, FNBHealthAlertState

log = logging.getLogger(__name__)

_DEFAULT_RECIPIENTS = [
    'pganesharajah@alphadirect.co.bw',   # CFO
    'ktshutlhedi@alphadirect.co.bw',     # Kago Tshutlhedi
    'pkago@alphadirect.co.bw',           # Pako Kago
]
_REMIND_EVERY = timedelta(hours=6)
_WINDOW_MIN = 90

# A batch still in a non-terminal state this long after we sent it is stuck.
# FNB answers `425 Too Early. Retry-After 120 seconds` to retrieveReport for a
# while after a submit, so anything under an hour is normal and alerting on it
# would train everyone to ignore the alert. Override per-site with
# settings.FNB_STUCK_BATCH_ALERT_HOURS or the command's --stuck-hours.
_STUCK_AFTER_HOURS = 2

# Non-terminal: FNB may still have news, or we never got an answer at all.
# SETTLED / FAILED / CANCELLED are terminal. PENDING never left the building.
_STUCK_STATUSES = (
    FNBBatchSubmission.Status.SUBMITTED,
    FNBBatchSubmission.Status.ACKNOWLEDGED,
    FNBBatchSubmission.Status.UNKNOWN,
)


def compute_verdict() -> tuple[str, dict]:
    """Return ('up'|'down', meta) from the last 90 minutes of FNB calls."""
    since = timezone.now() - timedelta(minutes=_WINDOW_MIN)
    rows = list(FNBSyncLog.objects.filter(created_at__gte=since)
                .values('status', 'http_status'))
    succ = sum(1 for r in rows if r['status'] == FNBSyncLog.Status.SUCCESS)
    hard = 0
    for r in rows:
        if r['status'] == FNBSyncLog.Status.SUCCESS:
            continue
        code = r['http_status']
        if (r['status'] == FNBSyncLog.Status.TIMEOUT or code is None or (code or 0) >= 500):
            hard += 1
    down = (succ == 0 and hard >= 3)
    return ('down' if down else 'up',
            {'successes': succ, 'hard_failures': hard, 'total': len(rows)})


def _compose_email(verdict: str, meta: dict) -> tuple[str, str]:
    """(subject, body). DeepSeek writes the body; falls back to a plain template."""
    signals = collect_safe_signals()
    if verdict == 'down':
        subject = 'FNB bank connection is DOWN'
        ask = ('Write a SHORT alert (under 90 words) telling the CFO the FNB bank '
               'connection appears to be down — the bank is not responding. Say '
               'plainly what still works (if statements are importing, say so), that '
               'this is on FNB\'s side, and that no action is needed from us beyond '
               'watching, unless it stays down. No jargon, no HTTP codes.')
    else:
        subject = 'FNB bank connection has RECOVERED'
        ask = ('Write a SHORT note (under 60 words) telling the CFO the FNB bank '
               'connection is working again. Plain English, no jargon.')

    text = _signals_to_text(signals)
    safety = is_safe_for_ai(text)
    fallback = (
        f'The FNB bank connection is {"not responding (down)" if verdict == "down" else "working again"}.\n\n'
        f'Signals: {meta["successes"]} good calls and {meta["hard_failures"]} '
        f'no-responses in the last {_WINDOW_MIN} minutes.\n\n'
        'Regards,\nOmni (Alpha Direct)'
    )
    if not safety.safe:
        return subject, fallback
    try:
        body = reasoning_complete(
            f'Current FNB signals:\n\n{safety.redacted_text}\n\n{ask}',
            system_prompt=('You are Aria, Alpha Direct\'s finance assistant, writing a '
                           'bank-connection alert email to the CFO and two finance '
                           'leads. Point-form where useful. Close "Regards, Aria (Omni)".'),
            max_tokens=320, feature='fnb_down_alert',
        )
        return subject, (body or '').strip() or fallback
    except DeepSeekUnavailable:
        return subject, fallback


def _run_link_watch(*, dry_run: bool = False) -> dict:
    """Evaluate the up/down link verdict + (maybe) email. Never raises."""
    verdict, meta = compute_verdict()
    state = FNBHealthAlertState.load()
    prev = state.state
    now = timezone.now()
    recipients = list(getattr(settings, 'FNB_HEALTH_ALERT_RECIPIENTS', _DEFAULT_RECIPIENTS))

    result = {'verdict': verdict, 'prev': prev, 'meta': meta, 'emailed': False, 'action': ''}

    # First-ever run: record baseline, stay silent.
    if prev == FNBHealthAlertState.State.UNKNOWN:
        result['action'] = 'baseline'
        if not dry_run:
            state.state = verdict
            state.changed_at = now
            state.save(update_fields=['state', 'changed_at', 'updated_at'])
        return result

    changed = (verdict != prev)
    remind = (verdict == 'down'
              and state.last_notified_at
              and (now - state.last_notified_at) >= _REMIND_EVERY)

    if not (changed or remind):
        result['action'] = 'no change'
        return result

    subject, body = _compose_email(verdict, meta)
    result['action'] = 'transition' if changed else 'reminder'
    if dry_run:
        result['subject'] = subject
        result['body'] = body
        return result

    try:
        send_with_cfo_cc(subject=subject, body=body, to=recipients,
                         from_email='pganesharajah@alphadirect.co.bw')
        result['emailed'] = True
    except Exception as exc:  # noqa: BLE001
        log.warning('fnb health watch: email failed (%s)', exc)

    state.state = verdict
    state.changed_at = now if changed else state.changed_at
    state.last_notified_at = now
    state.detail = f'{meta["successes"]} ok / {meta["hard_failures"]} no-response (90m)'
    state.save(update_fields=['state', 'changed_at', 'last_notified_at', 'detail', 'updated_at'])
    return result


# ---------------------------------------------------------------------------
# Stuck batches — the silence one hop past the poll sweep
# ---------------------------------------------------------------------------
#
# poll_fnb_batches (2026-08-20) made an unread REJECT visible. It did not make
# an unanswered batch visible. FNB replies `425 Too Early. Retry-After 120
# seconds` to retrieveReport for a while after a submit; on batch
# ALPHA-EFT-20260820-02f48be99baf4a66 it did so on every attempt for over ten
# minutes. The sweep logs and moves on exactly as designed — `1 checked, 0
# changed, 1 errored` — and the batch stays `submitted`. If FNB never starts
# answering, the batch sits there forever, its payments still carry
# bank_submitted_at so they cannot be re-sent, and nobody is told. Same failure
# class as the reject nobody read, one hop later.
#
# The body is written by hand, NOT by the reasoning cascade that writes the
# up/down alert: the whole point is that the batch key, count, total and last
# poll error arrive intact, and a summariser is free to drop or reword them.


def _stuck_threshold_hours(explicit: float | None = None) -> float:
    if explicit is not None:
        return float(explicit)
    return float(getattr(settings, 'FNB_STUCK_BATCH_ALERT_HOURS', _STUCK_AFTER_HOURS))


def _last_poll_error(idempotency_key: str) -> str:
    """The most recent unsuccessful FNB call about this batch, in plain words.

    refresh_batch_status logs through FNBClient.request, which stamps
    request_summary as 'Status poll for batch <key>' and, on a non-2xx, writes
    http_status + the response text. That is the only place the 425 is kept —
    the sweep itself only prints it to a log file nobody reads.
    """
    row = (FNBSyncLog.objects
           .filter(request_summary__contains=idempotency_key)
           .exclude(status=FNBSyncLog.Status.SUCCESS)
           .order_by('-created_at')
           .first())
    if row is None:
        return ('no poll error recorded — nothing has managed to ask FNB '
                'about this batch yet')
    bits = []
    if row.http_status:
        bits.append(f'HTTP {row.http_status}')
    msg = ' '.join((row.error_message or '').split())
    if msg:
        bits.append(msg[:200])
    return ' — '.join(bits) if bits else f'{row.status} (no detail recorded)'


def find_stuck_batches(threshold_hours: float | None = None) -> list[dict]:
    """Batches in a non-terminal state longer than the threshold.

    Ageing uses COALESCE(submitted_at, created_at) deliberately.
    submit_eft_batch writes submitted_at ONLY on the success path, so a batch
    left in UNKNOWN (the POST left, no clean answer came back, the money MAY
    have moved) has submitted_at = NULL for good. Ageing on submitted_at alone
    would silently skip the most dangerous status of the three.
    """
    hours = _stuck_threshold_hours(threshold_hours)
    cutoff = timezone.now() - timedelta(hours=hours)
    rows = (FNBBatchSubmission.objects
            .filter(status__in=_STUCK_STATUSES)
            .annotate(sent_at=Coalesce('submitted_at', 'created_at'))
            .filter(sent_at__lt=cutoff)
            .order_by('sent_at'))
    now = timezone.now()
    out = []
    for b in rows:
        out.append({
            'key':        b.idempotency_key,
            'status':     b.status,
            'count':      b.payment_count,
            'total':      b.total_amount_bwp,
            'currency':   b.currency_code or 'BWP',
            'sent_at':    b.sent_at,
            'age_hours':  (now - b.sent_at).total_seconds() / 3600,
            'reference':  b.fnb_reference or '(none issued)',
            'last_error': _last_poll_error(b.idempotency_key),
        })
    return out


def _compose_stuck_email(stuck: list[dict], hours: float) -> tuple[str, str]:
    n = len(stuck)
    subject = (f'FNB: {n} payment batch{"es" if n != 1 else ""} unconfirmed for '
               f'over {hours:g} hours')
    lines = [
        f'{n} FNB payment batch{"es have" if n != 1 else " has"} been sitting '
        f'unconfirmed for more than {hours:g} hours. FNB has not told us whether '
        'the money moved.',
        '',
        'Until each one resolves its payments stay marked as sent to the bank, '
        'so they cannot be re-sent.',
        '',
    ]
    for b in stuck:
        lines += [
            f'Batch {b["key"]}',
            f'  Status         : {b["status"]} for {b["age_hours"]:.1f} hours',
            f'  Payments       : {b["count"]}',
            f'  Total          : {b["currency"]} {b["total"]:,.2f}',
            f'  FNB reference  : {b["reference"]}',
            f'  Last poll error: {b["last_error"]}',
            '',
        ]
    lines += [
        'What to do: ask FNB to confirm the outcome of each batch above by its '
        'reference before anyone re-sends a payment. A batch shown as "unknown" '
        'may already have been paid.',
        '',
        'Regards,',
        'Omni (Alpha Direct)',
    ]
    return subject, '\n'.join(lines)


def check_stuck_batches(*, threshold_hours: float | None = None,
                        dry_run: bool = False) -> dict:
    """Alert once per newly-stuck batch. Never raises.

    De-duplication follows the up/down watcher (one singleton row, a quiet
    window) with one difference: it keys on WHICH batches are stuck, not merely
    on having emailed recently. Keying on time alone would swallow a second
    stuck batch that appeared inside the quiet window.
    """
    hours = _stuck_threshold_hours(threshold_hours)
    stuck = find_stuck_batches(hours)
    state = FNBHealthAlertState.load()
    now = timezone.now()
    keys = sorted(b['key'] for b in stuck)
    previously = set((state.stuck_keys or '').split())
    fresh = [k for k in keys if k not in previously]

    result = {'stuck_count': len(stuck), 'keys': keys, 'threshold_hours': hours,
              'emailed': False, 'action': ''}

    if not stuck:
        result['action'] = 'none stuck'
        # Clear silently — the batch resolving is already visible on the screen
        # and in the poll sweep's own output; a second "all clear" email would
        # only add noise.
        if not dry_run and (state.stuck_keys or state.stuck_notified_at):
            state.stuck_keys = ''
            state.stuck_notified_at = None
            state.save(update_fields=['stuck_keys', 'stuck_notified_at', 'updated_at'])
        return result

    remind = (state.stuck_notified_at is not None
              and (now - state.stuck_notified_at) >= _REMIND_EVERY)
    if not (fresh or remind):
        result['action'] = 'already alerted'
        if not dry_run and (state.stuck_keys or '').split() != keys:
            # A batch resolved but others remain — keep the recorded set honest
            # without emailing.
            state.stuck_keys = '\n'.join(keys)
            state.save(update_fields=['stuck_keys', 'updated_at'])
        return result

    subject, body = _compose_stuck_email(stuck, hours)
    result['action'] = 'new' if fresh else 'reminder'
    result['subject'] = subject
    result['body'] = body
    if dry_run:
        return result

    recipients = list(getattr(settings, 'FNB_HEALTH_ALERT_RECIPIENTS', _DEFAULT_RECIPIENTS))
    try:
        sent = send_with_cfo_cc(subject=subject, body=body, to=recipients,
                                from_email='pganesharajah@alphadirect.co.bw')
        result['emailed'] = bool(sent)
    except Exception as exc:  # noqa: BLE001
        log.warning('fnb stuck-batch watch: email failed (%s)', exc)

    # Stamp "already told them" ONLY on a send that actually went out. Writing
    # it regardless would mute this alert until the 6h reminder — a watcher
    # that silences itself on a failed send is the exact bug being fixed here.
    # send() returns 0 on a silent failure, so a falsy return is not a send.
    if not result['emailed']:
        result['action'] += ' (send failed — will retry next run)'
        return result

    state.stuck_keys = '\n'.join(keys)
    state.stuck_notified_at = now
    state.save(update_fields=['stuck_keys', 'stuck_notified_at', 'updated_at'])
    return result


def run_watch(*, dry_run: bool = False,
              stuck_hours: float | None = None) -> dict:
    """The whole FNB health watch: is the link up, AND is anything stuck.

    Both run on every pass. Keeping the stuck check inside run_watch (rather
    than only in the management command) means it cannot be left unscheduled
    by a caller who reasonably believes run_watch IS the watch.
    """
    result = _run_link_watch(dry_run=dry_run)
    try:
        result['stuck'] = check_stuck_batches(dry_run=dry_run,
                                              threshold_hours=stuck_hours)
    except Exception as exc:  # noqa: BLE001
        # A failure here must never take the link alert down with it.
        log.exception('fnb stuck-batch watch failed')
        result['stuck'] = {'stuck_count': 0, 'emailed': False,
                           'action': f'error: {type(exc).__name__}: {exc}'}
    return result
