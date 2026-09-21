"""
hris/late_notice_report.py — who keeps asking to be forgiven (CFO 2026-09-09).

Rule 1b forgives the first three late mornings a month if the person tells Omni
before 09:00. The CFO's concern, in his words: "we will keep a track of them,
create a dashboard for people who is always asking forgiveness so we nail them
on Nov performence feedback."

So this is a WATCHING report, not a scoring one. Nothing here deducts a point.
It exists so that by November there is a record of who used the button as a
genuine heads-up and who used it as a routine, and the difference is visible
before anyone is judged on it.

What makes somebody stand out is deliberately NOT the raw count:

  * Somebody with three notices in one month has used their whole allowance and
    is at the line — that matters more than three spread over three months.
  * Somebody who files at 08:58 every single day is following the rule to the
    letter and defeating it in spirit; the CLUSTERING of filing times says that,
    the count does not.
  * Somebody whose notices are nearly all OUT of time is not being forgiven at
    all — they are already being marked, and they need telling, not nailing.

The narrative is written by the local/DeepSeek chain (reasoning_complete), and
it is given ONLY aggregates — never a reason in somebody's own words, and never
a health detail. A sick-day reason is exactly the sort of thing that must not
travel to an outside model (AD-POL-AI-GOV-001).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from django.utils import timezone

# Someone at or over the monthly allowance in a month is "at the line".
MONTHLY_ALLOWANCE = 3
# Filing this close to the 09:00 cutoff, repeatedly, is a pattern worth naming.
LATE_FILER_MINUTES = 15


def collect(months: int = 3, today: dt.date | None = None) -> dict:
    """Per-person late-notice behaviour over the last `months` whole months."""
    from hris.late_notice_models import NOTICE_CUTOFF, LateNotice

    today = today or timezone.localdate()
    start = (today.replace(day=1) - dt.timedelta(days=31 * max(months - 1, 0))).replace(day=1)

    rows = (LateNotice.objects
            .filter(notice_date__gte=start)
            .select_related('profile__employee')
            .values_list('profile_id', 'profile__employee__full_name',
                         'notice_date', 'kind', 'in_time', 'filed_local_time'))

    cutoff_min = NOTICE_CUTOFF.hour * 60 + NOTICE_CUTOFF.minute
    people: dict = defaultdict(lambda: {
        'name': '', 'total': 0, 'in_time': 0, 'out_of_time': 0,
        'by_month': defaultdict(int), 'kinds': defaultdict(int),
        'near_cutoff': 0, 'dates': [],
    })

    for pid, name, d, kind, in_time, filed in rows:
        p = people[pid]
        p['name'] = name or '(unmatched)'
        p['total'] += 1
        p['in_time' if in_time else 'out_of_time'] += 1
        p['by_month'][f'{d.year}-{d.month:02d}'] += 1
        p['kinds'][kind] += 1
        p['dates'].append(d)
        if filed is not None:
            mins = filed.hour * 60 + filed.minute
            if 0 <= cutoff_min - mins <= LATE_FILER_MINUTES:
                p['near_cutoff'] += 1

    out = []
    for pid, p in people.items():
        months_used = dict(p['by_month'])
        worst = max(months_used.values()) if months_used else 0
        out.append({
            'profile_id': str(pid),
            'name': p['name'],
            'total': p['total'],
            'in_time': p['in_time'],
            'out_of_time': p['out_of_time'],
            'months': months_used,
            'worst_month': worst,
            # At or past the allowance in any single month.
            'hit_the_cap': worst >= MONTHLY_ALLOWANCE,
            'near_cutoff': p['near_cutoff'],
            'kinds': dict(p['kinds']),
            'first': min(p['dates']).isoformat() if p['dates'] else None,
            'last': max(p['dates']).isoformat() if p['dates'] else None,
        })

    # Worst month first, then volume — the person at the line outranks the
    # person with more notices spread thinly.
    out.sort(key=lambda r: (-r['worst_month'], -r['total'], r['name']))
    return {
        'from': start.isoformat(),
        'to': today.isoformat(),
        'people': out,
        'totals': {
            'people': len(out),
            'notices': sum(r['total'] for r in out),
            'at_the_cap': sum(1 for r in out if r['hit_the_cap']),
            'filed_too_late': sum(r['out_of_time'] for r in out),
        },
    }


def narrative(report: dict) -> str:
    """A short read of the pattern, from the cheap-first reasoning chain.

    Aggregates only. No reasons in anyone's own words, nothing about health.
    A failure here returns '' — the dashboard must still render its numbers
    when the model is unreachable.
    """
    from core.ai_assist import reasoning_complete

    people = report.get('people') or []
    if not people:
        return ''
    lines = [
        f"{r['name']}: {r['total']} notices, worst single month {r['worst_month']}, "
        f"{r['out_of_time']} filed after the cutoff, {r['near_cutoff']} filed in the "
        f"last 15 minutes before it"
        for r in people[:25]
    ]
    prompt = (
        "You are reviewing attendance-notice patterns at an insurance company in "
        "Botswana. Staff may tell the system before 09:00 that they will be late; "
        "the first three such mornings a month are forgiven.\n\n"
        "Data (aggregates only):\n" + "\n".join(lines) + "\n\n"
        "In at most 120 words, say which patterns a manager should look at before "
        "the November reviews, and which look harmless. Distinguish somebody using "
        "the notice as a genuine heads-up from somebody using it as a routine. Do "
        "not invent numbers. Do not speculate about anyone's health or personal "
        "circumstances. Plain English, no bullet points."
    )
    try:
        return (reasoning_complete(prompt, feature='late-notice-pattern') or '').strip()
    except Exception:      # noqa: BLE001 — the numbers must render regardless
        return ''
