"""
bonu/queries.py — turning a finding into a letter, and a letter into money back.

A dashboard full of red flags recovers nothing. The recovery happens when a firm receives
a specific, polite, dated question it has to answer in writing. So this module does three
small things and nothing else:

  1. Groups a firm's open findings into ONE letter (nobody answers eleven emails).
  2. Writes that letter in plain language — a question per finding, never an accusation.
     "Suspicious" cannot be answered. "Matter 118/2026 carries three charges for work on
     4 May, please confirm these are separate pieces of work" can.
  3. Gives it a reply date and a chase list, because an undated query is a query that
     gets filed.

**Nothing here sends anything.** It builds the draft and returns it. Sending is an outward
act that a person authorises — see `views.mark_query_sent`.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from django.utils import timezone

Z = Decimal('0')
REPLY_DAYS = 10                # working practice: ten days to answer a fee query
CHASE_AFTER_DAYS = 3           # then chase every three days
MAX_FINDINGS_PER_LETTER = 12   # more than this and the letter stops being answerable


def next_reference(firm, existing_count: int, today=None) -> str:
    """BONU-Q-2026-08-<firm initials>-<n>. Readable in a filing cabinet and unique per firm."""
    today = today or timezone.localdate()
    initials = ''.join(w[0] for w in (firm.name or 'X').split()[:3]).upper() or 'X'
    return f'BONU-Q-{today:%Y-%m}-{initials}-{existing_count + 1:02d}'


def compose(firm, findings, today=None, reply_days=REPLY_DAYS):
    """Build the letter. Returns a dict ready to become a QueryLetter row.

    The tone is deliberate: we are asking a professional firm to explain its own bill, not
    accusing anybody. A firm that receives an accusation lawyers up; a firm that receives a
    clear question usually answers it, and often credits the line.
    """
    today = today or timezone.localdate()
    picked = sorted(findings, key=lambda f: -(f.amount_at_risk or Z))[:MAX_FINDINGS_PER_LETTER]
    total = sum((f.amount_at_risk or Z) for f in picked)
    due = today + dt.timedelta(days=reply_days)

    questions = []
    for i, f in enumerate(picked, start=1):
        ask = (f.question_for_firm or '').strip() or (
            f'Please explain the charge described as "{f.title}".')
        amount = f' (P{f.amount_at_risk:,.2f})' if (f.amount_at_risk or Z) > Z else ''
        questions.append(f'{i}. {ask}{amount}')

    body = '\n'.join([
        f'Dear {firm.name},',
        '',
        'We are reviewing the fees billed to the BONU legal benefit. A few items need your',
        'confirmation before we can pass them for payment. These are questions, not',
        'conclusions — if the explanation is straightforward, please say so and we will',
        'close the query.',
        '',
        *questions,
        '',
        (f'Total amount held pending your answer: P{total:,.2f}.' if total > Z
         else 'No amount is being held pending your answer.'),
        '',
        f'Please reply by {due:%d %B %Y}. If any item needs a longer look, tell us which one',
        'and when you can answer it, and we will hold that line only.',
        '',
        'Regards,',
        'Alpha Direct Insurance Company (Pty) Ltd',
    ])

    return {
        'firm': firm,
        'subject': f'BONU fee query — {len(picked)} item(s), P{total:,.2f} held',
        'body': body,
        'amount_queried': total,
        'reply_due_on': due,
        'findings': picked,
        'left_out': max(0, len(list(findings)) - len(picked)),
    }


def draft_for_firm(firm, findings, existing_count=0, today=None):
    """compose() plus a reference. Kept separate so compose() stays easy to test."""
    out = compose(firm, findings, today=today)
    out['reference'] = next_reference(firm, existing_count, today=today)
    return out


def chase_list(queries, as_of=None):
    """Which letters need chasing today, and what to say.

    A letter is chased when it is past its reply date and the last chase was at least a few
    days ago. Escalation is by count, not by mood: two chases and it goes on the CFO's list.
    """
    as_of = as_of or timezone.localdate()
    out = []
    for q in queries:
        if q.status != q.Status.SENT or not q.reply_due_on:
            continue
        days_over = (as_of - q.reply_due_on).days
        if days_over < 0:
            continue
        since_chase = (as_of - q.last_chased_on).days if q.last_chased_on else None
        if since_chase is not None and since_chase < CHASE_AFTER_DAYS:
            continue
        out.append({
            'query_id': str(q.pk),
            'reference': q.reference,
            'firm': q.firm.name,
            'amount_queried': q.amount_queried,
            'days_overdue': days_over,
            'chased_count': q.chased_count,
            'escalate_to_cfo': q.chased_count >= 2,
            'line': (f'{q.firm.name} — {q.reference}, P{q.amount_queried:,.2f}, '
                     f'{days_over} day(s) past the reply date'
                     + (' — escalate' if q.chased_count >= 2 else '')),
        })
    out.sort(key=lambda r: (-r['days_overdue'], -(r['amount_queried'] or Z)))
    return out


def recovery_summary(queries):
    """Did asking work? The only number that justifies the exercise."""
    qs = list(queries)
    queried = sum((q.amount_queried or Z) for q in qs)
    conceded = sum((q.amount_conceded or Z) for q in qs)
    answered = [q for q in qs if q.status in ('conceded', 'rejected')]
    return {
        'letters': len(qs),
        'open': sum(1 for q in qs if q.status in QueryLetter_OPEN),
        'amount_queried': queried,
        'amount_recovered': conceded,
        'recovery_rate': (conceded / queried * 100).quantize(Decimal('0.1')) if queried else None,
        'answered': len(answered),
        'still_waiting': sum(1 for q in qs if q.status == 'sent'),
    }


# Kept as a module constant rather than importing the model here: this module is pure
# text-and-arithmetic and stays importable without Django's app registry loaded.
QueryLetter_OPEN = ('draft', 'sent')
