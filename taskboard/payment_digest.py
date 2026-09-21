"""The CFO's one-a-day view of every payment request.

WHY (CFO 2026-08-09): *"I have more than seven accountants requesting for
payments, and it is too much overwhelming for me to go and check everything."*
He opens one email instead of every request.

TWO RULES THIS FILE OBEYS
  1. **The numbers are computed here, never by a model.** Totals, ageing,
     duplicates and who-is-waiting-on-whom are arithmetic. The AI is given the
     finished figures and asked only to say what they mean in plain English. A
     model that adds up money is a model that will eventually add it up wrong.
  2. **If the AI is unavailable the digest still goes out**, with the numbers and
     a written fallback. A control that only works when an external service is
     up is not a control.

Used by the `payment_daily_digest` command (email) and by the summary panel on
/payment-requests, so the screen and the email can never disagree.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

log = logging.getLogger(__name__)

# Waiting on the CFO for longer than this is called out by name.
STALE_DAYS = 3


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception as exc:                             # noqa: BLE001
        # A money figure quietly rendering as 0.00 in the CFO's digest is a
        # swallow, not a default. Say so (Fable 2026-08-09).
        log.warning('payment digest: could not read amount %r (%s) — counted as 0', v, exc)
        return Decimal('0')


def collect(now=None) -> dict:
    """Every open payment request, plus what settled in the last 24 hours.

    Returns plain data — no HTML, no model calls — so the email, the screen and
    the tests all read the same thing.
    """
    from .models import PaymentRequest

    now = now or timezone.localtime()
    today = now.date()
    yesterday = now - timedelta(days=1)

    open_qs = (PaymentRequest.objects
               .filter(status__in=[PaymentRequest.Status.PENDING_FINANCE,
                                   PaymentRequest.Status.PENDING_CFO,
                                   # With the exception committee (a changed
                                   # bank account). Invisible to every counter
                                   # until Fable 5.1 audit 2026-09-02, M2.
                                   PaymentRequest.Status.EXCEPTION])
               .select_related('created_by')
               .order_by('created_at'))

    waiting_cfo, waiting_finance, waiting_committee = [], [], []
    by_entity = defaultdict(lambda: {'count': 0, 'total': Decimal('0')})
    by_loader = defaultdict(lambda: {'count': 0, 'total': Decimal('0')})
    by_currency = defaultdict(Decimal)
    overrides, stale, no_due_date = [], [], []

    for pr in open_qs:
        # localtime() both sides — see taskboard/escalation.py: `today` is the
        # Gaborone date while created_at.date() is the UTC date, so between
        # 00:00 and 02:00 CAT every request read a day older than it is.
        age = (today - timezone.localtime(pr.created_at).date()).days
        row = {
            'ref': pr.ref, 'entity': pr.entity or '—', 'payee': pr.payee or pr.subject or '—',
            'currency': pr.currency, 'total': _d(pr.total), 'age_days': age,
            'loader': pr.inputter or (pr.created_by.get_full_name() if pr.created_by else '—'),
            'verifier': pr.verifier or '—', 'category': pr.category or '—',
            'due_date': pr.due_date.isoformat() if pr.due_date else None,
            'lines': len(pr.line_items or []),
            'status': pr.status,
            # The OmniTask this request was routed on. Carried so the daily
            # digest can offer the CFO's one-tap decision on the rows waiting
            # on him; the screen panel ignores it. Only the id — the digest
            # re-reads the task and re-checks the gate before it renders a
            # link, so a stale row can never carry a live button.
            'task_id': pr.task_id,
        }
        if pr.status == PaymentRequest.Status.PENDING_CFO:
            waiting_cfo.append(row)
        elif pr.status == PaymentRequest.Status.EXCEPTION:
            waiting_committee.append(row)
        else:
            waiting_finance.append(row)

        by_entity[row['entity']]['count'] += 1
        by_entity[row['entity']]['total'] += row['total']
        by_loader[row['loader']]['count'] += 1
        by_loader[row['loader']]['total'] += row['total']
        by_currency[pr.currency] += row['total']

        # A duplicate override on an OPEN request is the single most important
        # line in this email — it is the control that failed on 7 Aug.
        if (pr.duplicate_override_reason or '').strip():
            overrides.append({
                **row,
                'reason': (pr.duplicate_override_reason or '')[:200],
                'category_label': pr.duplicate_override_category or '(none given)',
                'countersigned': bool(pr.duplicate_override_approved_by_id),
            })
        if pr.status == PaymentRequest.Status.PENDING_CFO and age >= STALE_DAYS:
            stale.append(row)
        if not pr.due_date:
            no_due_date.append(row)

    settled = list(PaymentRequest.objects
                   .filter(status__in=[PaymentRequest.Status.PAID,
                                       PaymentRequest.Status.REJECTED,
                                       PaymentRequest.Status.CANCELLED],
                           updated_at__gte=yesterday)
                   .values('ref', 'status', 'entity', 'currency', 'total', 'payee'))

    return {
        'generated_at': now,
        'waiting_cfo': waiting_cfo,
        'waiting_finance': waiting_finance,
        'waiting_committee': waiting_committee,
        'total_waiting_cfo': sum((r['total'] for r in waiting_cfo), Decimal('0')),
        'total_waiting_finance': sum((r['total'] for r in waiting_finance), Decimal('0')),
        'total_waiting_committee': sum((r['total'] for r in waiting_committee), Decimal('0')),
        'by_entity': dict(by_entity),
        'by_loader': dict(by_loader),
        'by_currency': {k: v for k, v in by_currency.items()},
        'overrides': overrides,
        'stale': stale,
        'no_due_date': no_due_date,
        'settled_24h': settled,
        'open_count': len(waiting_cfo) + len(waiting_finance) + len(waiting_committee),
    }


def _facts_for_ai(d: dict) -> str:
    """The finished figures, as text. The model reads this and nothing else."""
    lines = [
        f"Open payment requests: {d['open_count']}",
        f"Waiting on the CFO: {len(d['waiting_cfo'])} worth {d['total_waiting_cfo']:,.2f}",
        f"Waiting on finance sign-off: {len(d['waiting_finance'])} worth {d['total_waiting_finance']:,.2f}",
        f"With the exception committee (changed bank account): {len(d.get('waiting_committee', []))} worth {d.get('total_waiting_committee', 0):,.2f}",
        f"Duplicate overrides sitting on OPEN requests: {len(d['overrides'])}",
        f"Waiting on the CFO more than {STALE_DAYS} days: {len(d['stale'])}",
        f"Open requests with no due date: {len(d['no_due_date'])}",
        f"Settled in the last 24 hours: {len(d['settled_24h'])}",
        "",
        "By entity: " + "; ".join(
            f"{k} {v['count']} req {v['total']:,.2f}" for k, v in sorted(d['by_entity'].items())),
        "By person who loaded it: " + "; ".join(
            f"{k} {v['count']} req {v['total']:,.2f}" for k, v in sorted(d['by_loader'].items())),
    ]
    if d['overrides']:
        lines.append("")
        lines.append("Duplicate overrides in detail:")
        for o in d['overrides'][:10]:
            lines.append(
                f"  {o['ref']} {o['currency']} {o['total']:,.2f} to {o['payee']}, "
                f"loaded by {o['loader']}, reason category {o['category_label']}, "
                f"countersigned: {'yes' if o['countersigned'] else 'NO'}")
    if d['stale']:
        lines.append("")
        lines.append("Waiting on the CFO longest:")
        for s in sorted(d['stale'], key=lambda r: -r['age_days'])[:8]:
            lines.append(f"  {s['ref']} {s['currency']} {s['total']:,.2f} to {s['payee']}, "
                         f"{s['age_days']} days, loaded by {s['loader']}")
    return "\n".join(lines)


_SYSTEM = (
    "You write a short daily note for the CFO of a Botswana insurance company "
    "about payment requests waiting for him. You are given FINISHED FIGURES — "
    "never recalculate them, never invent a number that is not in the input, and "
    "never state a total that differs from the one given. Write 3 to 5 short "
    "sentences of plain English: what is waiting on him, what is unusual or "
    "worth his attention first, and anything that looks like it needs chasing. "
    "A duplicate override that is NOT countersigned is the most serious thing "
    "possible — say so plainly and first. No greeting, no sign-off, no bullet "
    "points, no markdown. Write as a colleague briefing him in the corridor."
)


def narrative(d: dict) -> tuple[str, str]:
    """(text, source). Falls back to a written summary if no engine answers.

    The figures already exist; this only says what they mean. If it fails the
    digest still goes out — see the module docstring.
    """
    facts = _facts_for_ai(d)
    try:
        # Every prompt goes through the PII firewall before it leaves — the
        # contract at the top of core.ai_assist, which every other caller obeys
        # and this one did not. `facts` carries payee names (some of them natural
        # people, on refunds and reimbursements) and the names of the staff who
        # loaded each request. Found by Fable 2026-08-09; until this line it was
        # sending them unmasked on EVERY page render, not just at 09:30.
        from core.ai_assist import is_safe_for_ai, reasoning_complete
        report = is_safe_for_ai(facts)
        text = reasoning_complete(report.redacted_text, system_prompt=_SYSTEM,
                                  max_tokens=320)
        if text and text.strip():
            return text.strip(), 'Aria'
    except Exception as exc:                             # noqa: BLE001
        log.warning('payment digest: AI narrative unavailable (%s) — using the '
                    'written fallback, the digest still goes out', exc)
    return _fallback_narrative(d), 'written'


def _fallback_narrative(d: dict) -> str:
    bits = []
    if d['waiting_cfo']:
        bits.append(f"{len(d['waiting_cfo'])} request(s) are waiting on you, "
                    f"totalling {d['total_waiting_cfo']:,.2f}.")
    else:
        bits.append("Nothing is waiting on you.")
    if d['waiting_finance']:
        bits.append(f"{len(d['waiting_finance'])} more are still with finance "
                    f"({d['total_waiting_finance']:,.2f}).")
    if d.get('waiting_committee'):
        bits.append(f"{len(d['waiting_committee'])} are with the exception committee "
                    f"(a changed bank account) — not blocked, waiting on three sign-offs "
                    f"({d['total_waiting_committee']:,.2f}).")
    uncounter = [o for o in d['overrides'] if not o['countersigned']]
    if uncounter:
        bits.append(f"{len(uncounter)} duplicate override(s) are NOT countersigned — "
                    f"look at those first.")
    elif d['overrides']:
        bits.append(f"{len(d['overrides'])} duplicate override(s) are on file, all countersigned.")
    if d['stale']:
        bits.append(f"{len(d['stale'])} have been with you more than {STALE_DAYS} days.")
    return " ".join(bits)
