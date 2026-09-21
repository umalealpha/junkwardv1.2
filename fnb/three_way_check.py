"""Daily three-way check: what Omni says, what the bank batch says, and the age.

CFO, 12-Sep-2026, on the FNB & Payments improvement plan: *"implement what is
legal and real, dont break whatever we have built previously."* This is the read
half of that plan's first P0 — it changes nothing and moves nothing. It finds
the places where Omni's own record and the bank's answer disagree, and the
batches that have been sitting long enough that somebody should own them.

Why READ ONLY, deliberately. The plan asks that settlement and rejection update
the payment request automatically. On production today that would silently
rewrite 27 live rows the moment it shipped, including two that a human decided
to CANCEL against a batch the bank has already settled. Rewriting a human's
decision because a machine thinks it knows better is how you lose the audit
trail. So this reports; a person decides. The automatic half is a separate,
explicit decision.

**Omni moves no money.** A payment request is a workflow record. Money leaves at
FNB, authorised by a person with two-factor. A contradiction here is a record
that is wrong — real, and worth fixing — not money gone astray.

Measured on production 13-Sep-2026 before this was written:
  paid in Omni  / batch FAILED      : 10   (the bank rejected it; Omni says done)
  paid in Omni  / batch SUBMITTED   : 11   (the bank never confirmed; Omni says done)
  NOT paid      / batch SETTLED     :  6   (the bank moved it; Omni is still open
                                            — and 2 of those 6 were CANCELLED)
  submitted batches                 : 46, 14 over seven days, BWP 2,098,616.97
  failed batches                    : 16, 12 over seven days, BWP   636,753.89
"""
from __future__ import annotations

import datetime

from django.utils import timezone


# How each disagreement reads to a person, and what it means for them. The
# wording is the point: "paid against a rejected batch" tells nobody what to do.
FINDINGS = {
    'paid_but_batch_failed': (
        'Marked paid in Omni, but the bank rejected the batch',
        'The money did not move. Either it still needs paying, or it was paid '
        'another way and the record needs correcting. Do not resubmit until you '
        'know which.'),
    'paid_but_batch_unconfirmed': (
        'Marked paid in Omni, but the bank has never confirmed it',
        'The bank has not told us whether the money moved. Check the account '
        'before anyone assumes this is done.'),
    'settled_but_not_paid': (
        'The bank settled it, but Omni still shows it as open',
        'The money HAS left the account. Somebody may chase or re-pay this '
        'because Omni says it is outstanding.'),
    'cancelled_but_settled': (
        'Cancelled in Omni, but the bank settled it',
        'Someone cancelled this after the money had already gone. It needs a '
        'human to say what actually happened.'),
}


def _age_days(dt):
    if not dt:
        return None
    return (timezone.now() - dt).days


def _within(max_age_days: int):
    """The oldest `created_at` whose printed age is still `max_age_days` or less.

    `_age_days` FLOORS to whole days, so a batch the tables print as "7d" is
    anything from 7.0 to 7.99 days old. Cutting at `now - 7 days` would drop
    rows the same email prints as 7d — a report that contradicts its own age
    column. The window therefore runs to `max_age_days + 1`, exclusive, which
    is exactly the set where `_age_days(dt) <= max_age_days`.
    """
    return timezone.now() - datetime.timedelta(days=max_age_days + 1)


def contradictions(max_age_days: int | None = None) -> dict:
    """Every payment request whose own status disagrees with its FNB batch.

    Ordered worst first: settled-but-cancelled, then settled-but-open, then
    paid-against-a-rejection, then paid-against-silence.

    `max_age_days` limits it to recent batches. It is None — everything — for
    the exception cockpit screen, and 7 for the daily email (CFO, 21-Sep-2026:
    *"this email should not talk about things 7 days old, going forward"*).
    The screen deliberately keeps the cap OFF: a problem nobody fixes must not
    be able to age quietly out of Omni altogether.
    """
    from taskboard.models import PaymentRequest

    base = (PaymentRequest.objects
            .filter(fnb_batch__isnull=False)
            .select_related('fnb_batch'))
    if max_age_days is not None:
        base = base.filter(fnb_batch__created_at__gt=_within(max_age_days))

    def rows(qs):
        out = []
        for p in qs.order_by('-total'):
            b = p.fnb_batch
            out.append({
                'ref': p.ref,
                'entity': p.entity,
                'subject': p.subject,
                # `payee` is blank on 321 of the 359 live payment requests -
                # it is simply not the field anyone fills in. The name of who is
                # being paid lives in `subject` (e.g. "G2026004923 BUILDERS MAPS
                # HARDWARE(B467BRZ)"). Shipped as `p.payee` alone, every row of
                # the cockpit and of the daily email carried an EMPTY Payee
                # column - a list of 27 problems with no way to tell whose money
                # each one is. Seen on the live screen, not in a test.
                # (13-Sep-2026.)
                'payee': p.payee or p.subject,
                'total': p.total,
                'omni_status': p.status,
                'batch_status': b.status,
                'batch_key': b.idempotency_key,
                'fnb_reference': b.fnb_reference,
                'failure_reason': b.failure_reason,
                'age_days': _age_days(b.created_at),
            })
        return out

    cancelled_settled = base.filter(status='cancelled', fnb_batch__status='settled')
    settled_not_paid = (base.filter(fnb_batch__status='settled')
                        .exclude(status__in=('paid', 'cancelled')))
    paid_failed = base.filter(status='paid', fnb_batch__status='failed')
    paid_unconfirmed = base.filter(status='paid',
                                   fnb_batch__status__in=('submitted', 'acknowledged',
                                                          'pending', 'unknown'))
    return {
        'cancelled_but_settled': rows(cancelled_settled),
        'settled_but_not_paid': rows(settled_not_paid),
        'paid_but_batch_failed': rows(paid_failed),
        'paid_but_batch_unconfirmed': rows(paid_unconfirmed),
    }


def open_batches(stale_days: int = 7, max_age_days: int | None = None) -> dict:
    """Batches the bank has not settled, and batches it rejected — with age,
    amount, who sent them, and the last thing FNB actually said.

    `unknown` is included on purpose: the POST left and no clean answer came
    back, so the money MAY have moved. Those are never auto-retried.
    """
    from fnb.models import FNBBatchSubmission as Batch

    cut = timezone.now() - datetime.timedelta(days=stale_days)
    groups = {}
    for status in ('submitted', 'acknowledged', 'pending', 'unknown', 'failed'):
        qs = (Batch.objects.filter(status=status)
              .select_related('source_account', 'submitted_by')
              .order_by('created_at'))
        if max_age_days is not None:
            qs = qs.filter(created_at__gt=_within(max_age_days))
        if not qs.exists():
            continue
        groups[status] = {
            'count': qs.count(),
            'total': sum((b.total_amount_bwp or 0) for b in qs),
            'over_threshold': qs.filter(created_at__lt=cut).count(),
            'rows': [{
                'key': b.idempotency_key,
                'payments': b.payment_count,
                'total': b.total_amount_bwp,
                'age_days': _age_days(b.created_at),
                'owner': (b.submitted_by.get_full_name() or b.submitted_by.get_username())
                         if b.submitted_by else 'unknown',
                'fnb_reference': b.fnb_reference,
                'last_word_from_fnb': (b.failure_reason or '')[:300],
            } for b in qs],
        }
    return {'stale_days': stale_days, 'groups': groups,
            'max_age_days': max_age_days}


def notification_health(stuck_hours: int = 2) -> dict:
    """Incoming FNB bank notifications that never moved past 'received', and
    ones that could not be classified at all — the visibility this check
    exists to provide (CFO's FNB & Payments plan, 2026-09-14: "a stuck record
    nobody can see is the actual failure").

    🔴 THE HISTORIC BACKLOG IS COUNTED SEPARATELY (CFO 2026-09-17).
    Measured on production 17-Sep-2026: 863 events sat on 'received', the
    oldest from 27-May and the newest 14-Sep 10:30 UTC; processing began at
    14-Sep 11:00 and 50 events have gone through cleanly since. The CFO's
    decision was "leave the old ones, fix it going forward".

    That decision breaks a single counter. With 863 permanently stuck, this
    check could never return to zero again, and an alarm that is always on is
    an alarm nobody reads — which is precisely how a four-month stall went
    unnoticed in the first place. So:

        `stuck`   = events received since the backlog date and still sitting.
                    This is the live alert and it SHOULD be 0.
        `backlog` = the known historic pile. Reported, dated, never alerted on,
                    and never silently folded into the live number.

    None of them are payment rejections, so nothing here can change whether a
    payment is paid: every one of the 863 is a camt054 cash notification
    (money in / money out). That was checked against production, not assumed —
    the CFO's own brief warned against reading a webhook backlog as proof the
    bank rejected anything.
    """
    from fnb.models import FNBWebhookEvent

    cut = timezone.now() - datetime.timedelta(hours=stuck_hours)
    backlog_before = _backlog_before()

    received = FNBWebhookEvent.objects.filter(
        status=FNBWebhookEvent.Status.RECEIVED)
    stuck = received.filter(received_at__lt=cut,
                            received_at__gte=backlog_before).count()
    backlog = received.filter(received_at__lt=backlog_before).count()
    failed = (FNBWebhookEvent.objects
              .filter(status=FNBWebhookEvent.Status.FAILED)
              .count())
    return {
        'stuck': stuck,
        'failed': failed,
        'stuck_hours': stuck_hours,
        'backlog': backlog,
        'backlog_before': backlog_before.date().isoformat(),
        'backlog_note': (
            'Bank notifications received before this date are a known historic '
            'pile the CFO chose to leave alone on 17-Sep-2026. They are cash '
            'notifications, not payment rejections, so no payment status '
            'depends on them.') if backlog else '',
    }


#: The moment the notification worker started keeping up.
#:
#: 🔴 A TIME, not a date. This was '2026-09-14', which midnight-UTC is ELEVEN
#: HOURS too early: the historic pile's newest event is 14-Sep 10:30 UTC and
#: processing only began at 11:00. Every backlog event received that morning
#: would have counted as a LIVE stall, so the alert could never read zero and
#: the screen would have printed "the worker has stalled" for ever — the exact
#: always-on alarm this split was built to remove (Fable 5.1, round 2).
_BACKLOG_BEFORE_DEFAULT = '2026-09-14T11:00:00+00:00'


def _backlog_before():
    """When the worker started keeping up. Settings-overridable so the line can
    be moved once the old pile is dealt with, without a deploy.

    Accepts a full timestamp or a bare date (a bare date means midnight UTC).
    """
    from django.conf import settings

    raw = str(getattr(settings, 'FNB_WEBHOOK_BACKLOG_BEFORE',
                      _BACKLOG_BEFORE_DEFAULT))
    dt = None
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        try:
            d = datetime.date.fromisoformat(raw)
            dt = datetime.datetime.combine(d, datetime.time.min)
        except (TypeError, ValueError):
            dt = datetime.datetime.fromisoformat(_BACKLOG_BEFORE_DEFAULT)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, datetime.timezone.utc)
    return dt


def rejected_but_request_not_open(stale_days: int = 0,
                                  max_age_days: int | None = None) -> dict:
    """Failed or unconfirmed bank batches whose payment request is NOT open.

    This is the CFO's read-only reconciliation (his FNB brief, control 8):
    *"failed batches linked to requests currently marked paid, pending CFO, or
    cancelled"*. It changes nothing and moves nothing — a person decides.

    It reads from the BATCH side, through the new
    FNBBatchSubmission.payment_request stamp, which is the whole point.
    `contradictions()` above reads from the request side through
    PaymentRequest.fnb_batch, and that FK holds exactly ONE batch. A request
    processed line-by-line produces one batch per line, so on 16-Sep-2026 a
    Choppies supplier request became four AG01 rejects and the cockpit could
    only ever see the first. Here they group back into ONE row carrying four
    instructions — the brief's *"a user should see one business problem, not
    four unrelated failures"*.

    Batches loaded before the stamp existed have no request on them; they are
    still covered by contradictions(), which is why this adds to that check
    rather than replacing it.
    """
    from fnb.models import FNBBatchSubmission as Batch

    NOT_OPEN = ('paid', 'pending_cfo', 'cancelled')
    qs = (Batch.objects
          .filter(status__in=('failed', 'unknown'),
                  payment_request__isnull=False,
                  payment_request__status__in=NOT_OPEN)
          .select_related('payment_request')
          .order_by('payment_request__ref', 'created_at'))
    if max_age_days is not None:
        qs = qs.filter(created_at__gt=_within(max_age_days))

    grouped: dict = {}
    for b in qs:
        pr = b.payment_request
        g = grouped.setdefault(pr.ref, {
            'ref': pr.ref,
            'entity': pr.entity,
            'payee': pr.payee or pr.subject,
            'total': pr.total,
            'omni_status': pr.status,
            'processing_method': pr.processing_method,
            'instructions': [],
            'bank_reasons': [],
            'evidence_recorded': bool((pr.decision_notes or '').strip()),
        })
        g['instructions'].append({
            'batch_key': b.idempotency_key,
            'batch_status': b.status,
            'amount': b.total_amount_bwp,
            'age_days': _age_days(b.created_at),
            'failure_reason': (b.failure_reason or '')[:300],
        })
        code = (b.failure_reason or '').split(':', 1)[0].strip()[:8]
        if code and code not in g['bank_reasons']:
            g['bank_reasons'].append(code)

    rows = sorted(grouped.values(), key=lambda r: r['total'] or 0, reverse=True)
    return {
        'rows': rows,
        'count': len(rows),
        'instruction_count': sum(len(r['instructions']) for r in rows),
        'total': sum((r['total'] or 0) for r in rows),
        # The wording has to match what the query actually selects. It includes
        # 'unknown', where the instruction left us and no clear answer came back
        # — for those the money MAY have moved, which is not the same claim as
        # "rejected". Saying "the money did not move" over a table containing
        # them would be a figure labelled as something it is not.
        'note': (
            'Read only. Nothing here has been changed. Each of these has a '
            'payment request that is closed — Paid, Pending CFO or Cancelled — '
            'while the bank either REJECTED every instruction behind it (the '
            'money did not move) or never gave a clear answer (the money MAY '
            'have moved, and a person must check with FNB before anything is '
            'resent). Some may genuinely have been paid another way; that needs '
            'the evidence recorded before the batch is treated as settled.'),
    }


def run(stale_days: int = 7, max_age_days: int | None = None) -> dict:
    """The whole check. Pure read; returns everything a person needs to act.

    `max_age_days=None` is everything — what the exception cockpit screen asks
    for. The daily email asks for 7 (CFO, 21-Sep-2026). `excluded_older` counts
    what the window left out, so the command's log can still prove nothing was
    dropped silently even on a run whose email says nothing about it.
    """
    c = contradictions(max_age_days=max_age_days)
    b = open_batches(stale_days=stale_days, max_age_days=max_age_days)
    n = notification_health()
    r = rejected_but_request_not_open(max_age_days=max_age_days)
    total_contradictions = sum(len(v) for v in c.values())
    excluded_older = 0
    if max_age_days is not None:
        excluded_older = (sum(len(v) for v in contradictions().values())
                          - total_contradictions)
    return {
        'checked_at': timezone.localtime().strftime('%d %b %Y %H:%M'),
        'max_age_days': max_age_days,
        'excluded_older': excluded_older,
        'contradictions': c,
        'contradiction_count': total_contradictions,
        'batches': b,
        'notifications': n,
        # The CFO's control 8, grouped one row per payment request. Kept as its
        # OWN key and deliberately NOT folded into contradiction_count: every
        # existing renderer prints "N payment(s) do not agree" off that number
        # and none of them was touched here. Widening a figure without tracing
        # the screens it feeds is the exact mistake Fable caught on 14-Sep.
        'rejected_not_open': r,
        # 'clean' is the ORIGINAL meaning (contradictions only) and stays
        # exactly what every existing renderer already trusts it to mean —
        # the payments/exceptions screen and the email both print "0
        # payment(s) do not agree" whenever 'clean' is True, and neither was
        # touched to say anything about bank notifications. Folding the new
        # notification check into 'clean' would make that headline lie the
        # day a notification gets legitimately flagged FAILED (Fable 5.1
        # review, 2026-09-14 — the class is "a flag widened without tracing
        # every renderer it feeds"). 'notifications_clean' is the SEPARATE
        # signal for the new check; callers that care about both check both.
        'clean': total_contradictions == 0,
        'notifications_clean': n['stuck'] == 0 and n['failed'] == 0,
    }
