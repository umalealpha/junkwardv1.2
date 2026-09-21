"""
hris/morning_brief.py

The 6:50am "Morning Brief" — a warm, gamified daily email for each employee
(CFO 2026-07-14). English with a light, CORRECT Setswana touch. Shows:
yesterday's Time Doctor hours vs target, this-week hours + rank, the company's
top performer, the employee's REAL annual-leave balance, tasks, taskboard
matters, POs waiting for them, and a short personalised note from Aria (the
omni assistant). Aggregates only — no Time Doctor window/app titles
(AD-POL-AI-GOV-001).

House style: Book Antiqua on navy #0D1B2A + orange #F4A623, proper tables,
mobile-responsive (most staff open this on a phone and tap through to their
details / bug report — CFO 2026-07-14). The "biggest-loser" naming stays on the
manager/CFO dashboard, never broadcast in the all-staff email.
"""

from __future__ import annotations

import datetime
import html as _html
from decimal import Decimal

from hris import workforce
from hris.workforce_brief import leave_summary, pending_tasks, announcements_for, holiday_off_dates
from django.utils import timezone

NAVY, ORANGE, INK, MUT = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280'
GREEN, AMBER, RED = '#059669', '#B45309', '#DC2626'
FONT = "'Book Antiqua','Palatino Linotype',Palatino,Georgia,serif"

# A light rotating Setswana touch. Corrected 2026-07-14 per a Setswana review:
# "Tlhola sentle" (spend the day well) is the precise "have a great day"; the
# openers below are all standard greetings.
_TSWANA_OPENERS = [
    ('Dumela', 'Hello'),
    ('Dumela, o tsogile jang?', 'Good morning, how did you wake?'),
    ('A o kae?', 'How are you?'),
    ('Go siame?', 'All good?'),
]


def opener(first_name: str, day: datetime.date):
    tsn, _en = _TSWANA_OPENERS[day.toordinal() % len(_TSWANA_OPENERS)]
    return f"{tsn} {first_name}!"


def day_status(required: Decimal, tracked: Decimal) -> dict:
    """(emoji, English, Setswana, colour) for yesterday's performance."""
    required = Decimal(required or 0)
    tracked = Decimal(tracked or 0)
    if required <= 0:
        return {'emoji': '🌤️', 'en': 'Rest day', 'tsn': 'Letsatsi la boikhutso', 'color': MUT}
    ratio = float(tracked) / float(required) if required else 0
    if ratio >= 1.0:
        return {'emoji': '🔥', 'en': 'On fire — target smashed!', 'tsn': 'O dirile sentle!', 'color': GREEN}
    if ratio >= 0.8:
        return {'emoji': '👍', 'en': 'On track', 'tsn': 'Go siame', 'color': GREEN}
    if ratio >= 0.5:
        return {'emoji': '⏳', 'en': "Let's pick it up today", 'tsn': 'Re ka dira botoka', 'color': AMBER}
    return {'emoji': '🌱', 'en': 'Fresh start today', 'tsn': 'Re simolola sesha', 'color': AMBER}


def badge(week_hours: Decimal, week_target: float = 32.5) -> str:
    # Weekly target is 5×6.5 = 32.5 (Mon-Fri) for staff, 5×4.5 = 22.5 for
    # managers (CFO 2026-07-23), +4 if Saturday is worked. Thresholds scale to
    # the recipient's own target so a manager can still earn "Full-week hero".
    wt = float(week_target or 32.5)
    wh = float(week_hours or 0)
    if wh >= wt:
        return '🏅 Full-week hero'
    if wh >= 0.6 * wt:
        return '⭐ Strong week'
    if wh >= 0.25 * wt:
        return '🌿 Building momentum'
    return ''


def aria_note(*, required, tracked, week_hours, rank, team_size, late_days, weekly) -> str:
    """A short, warm, personalised line from Aria (the omni assistant, DeepSeek/
    Gemini via the shared helper). NUMBERS ONLY — no names, no window/app data —
    so nothing identifiable leaves the box (AD-POL-AI-GOV-001). Returns '' on any
    failure; the brief renders fine without it."""
    from core.ai_assist import reasoning_complete, is_safe_for_ai
    period = 'this week' if weekly else 'yesterday'
    ctx = (f"tracked {tracked}h against a {required}h target {period}; "
           f"week total {week_hours}h; rank {rank or '?'} of {team_size or '?'}; "
           f"late starts after 08:15 this week: {late_days}.")
    system = (
        "You are Aria, Alpha Direct Insurance's friendly workplace assistant. "
        "Write ONE short sentence (max 22 words) for an employee's morning brief. "
        "Celebrate good work; if hours are low or there are repeated late starts, "
        "nudge gently with light, kind humour (e.g. note a third late start this week) "
        "— never harsh, never a threat. Plain English, no names, no lists, no emojis.")
    try:
        rep = is_safe_for_ai(ctx)
        if not getattr(rep, 'safe', True):
            return ''
        txt = reasoning_complete(getattr(rep, 'redacted_text', '') or ctx,
                                 system_prompt=system, timeout=12.0, max_tokens=80)
        return (txt or '').strip().strip('"').replace('\n', ' ')
    except Exception:    # noqa: BLE001
        return ''


#: Approval streams that are money leaving the building. Deliberately kept out
#: of the morning brief - CFO 2026-09-12: "my morning breif and cfo brief
#: should not include outstading payments rather outstading staff loan request,
#: leave request, incentive and commisison request that is the most important
#: thing." Payments are worked in Omni and released at FNB with the bank's own
#: two-factor; the brief is for the people decisions only he can take.
PAYMENT_STREAMS = {
    # Money leaving the building. Every key here exists in
    # core.approvals_views.pending_approvals_for - checked against the source,
    # not guessed.
    'payments',              # payment packs to sign
    'payment_requests',      # payment requests to authorise
    'petty_cash',            # petty cash vouchers
    'refunds',               # refunds awaiting approval
    'refunds_process',       # refunds to load in FNB
    'customer_refunds_fnb',  # loaded in FNB - authorise at the bank
    'spend',                 # spend & event requests
    'leave_encash_pay',      # the PAYMENT leg of an encashment (approval leg stays)
    'po',                    # purchase orders
    'journal_entries',       # ledger postings
}


def people_approvals_for(user) -> dict:
    """Staff decisions waiting on THIS person - leave, staff loans, incentives,
    commissions and the rest - from core.approvals_views.pending_approvals_for,
    the same aggregator the Omni app and the desktop "My Approvals" read. Money
    streams are filtered out (see PAYMENT_STREAMS). Best-effort: a failure here
    must never cost anyone their brief."""
    out = {'count': 0, 'streams': []}
    try:
        from core.approvals_views import pending_approvals_for
        for st in (pending_approvals_for(user) or []):
            if st.get('key') in PAYMENT_STREAMS:
                continue
            n = int(st.get('count') or 0)
            if n <= 0:
                continue
            out['streams'].append({'label': st.get('label') or '',
                                   'count': n,
                                   'href': st.get('href') or '',
                                   'oldest_days': int(st.get('oldest_days') or 0)})
            out['count'] += n
    except Exception:    # noqa: BLE001
        pass
    return out


def _leave_line(leave: dict) -> str:
    """Real annual-leave balance + this-month usage, in plain words."""
    avail = leave.get('annual_available')
    bits = []
    if avail is not None:
        ent = leave.get('annual_entitlement')
        tail = f" of {ent:g}" if ent else ''
        bits.append(f'Annual leave available now: <b>{avail:g}{tail} day(s)</b>')
    bits.append(f'Taken this month: <b>{leave["taken_month"]}</b>')
    if leave.get('pending_count'):
        bits.append(f'Pending: <b>{leave["pending_count"]}</b> request(s)')
    return ' · '.join(bits)


def build_morning_html(*, name, day, required, tracked_yesterday, week_hours,
                       rank, team_size, winner_name, leave, tasks, announcements, approvals,
                       yesterday_hero=None, dept_league=None, needs_boost=None,
                       authorities=None,
                       productive_hours=None,
                       started=None, finished=None, tracking_ok=True, tracking_pending=False, tracking_na=False, weekly=False,
                       is_manager=False, week_target=32.5,
                       late_days=0, aria='', tip=None, helpdesk_url='https://omni.alphadirect.co.bw/helpdesk/',
                       bug_url='https://omni.alphadirect.co.bw/report-bug',
                       my_brief_url='https://omni.alphadirect.co.bw/hris/my-brief') -> str:
    """The fresh-air morning email. Email-safe inline HTML + a small responsive
    <style> block so it stacks cleanly on a phone. First-name personal.
    weekly=True switches labels to a Mon-Sat weekly wrap."""
    esc = _html.escape
    first = esc((name or 'there').split()[0])
    winner_name = esc(winner_name or '—')
    period_label = ('This week (Mon–Sat) — productive hours' if weekly
                    else "Yesterday's productive hours")
    # Fall back to the clock ONLY when Time Doctor returned no productivity split,
    # so nobody's card can render blank.
    headline = productive_hours if productive_hours is not None else tracked_yesterday
    st = day_status(required, headline)
    pct = 0 if not required else min(100, int(round(100 * float(headline) / float(required))))
    bdg = badge(week_hours, week_target)

    if tracking_na:
        # CEO / CFO (CFO instruction, 11 Aug 2026). They do not run Time Doctor,
        # so before today the brief SKIPPED them entirely — `uid is None` — and
        # they received nothing at all. They still want the brief: the tasks, the
        # approvals, the leave, the team picture. What they must NOT be sent is
        # the red "we saw no tracking from you, raise an IT ticket" panel, which
        # is a false alarm for a role that was never tracked.
        hero = (
            f'<div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px;padding:16px">'
            f'<div style="font-size:15px;font-weight:800;color:{NAVY}">Your brief — no time tracking on your role</div>'
            f'<div style="font-size:13px;color:{INK};margin-top:6px">Time Doctor does not apply to you, '
            f'so there are no hours to report. Everything else below is yours: approvals waiting, '
            f'your tasks, leave, and how the team is doing.</div>'
            f'</div>')
    elif tracking_pending:
        # People-data guardrail (CFO 2026-08-01): the guard could NOT yet confirm
        # a real zero — the figure hasn't settled across the day's pulls, or their
        # own history says this is likely a late/stuck upload (the Keetile / Wangu
        # case). So we never tell them their tracker is broken. Neutral, no ticket,
        # nothing to do — a genuine zero surfaces the next day once it settles.
        hero = (
            f'<div style="background:#FFFBEB;border:1px solid {ORANGE};border-radius:12px;padding:16px">'
            f'<div style="font-size:15px;font-weight:800;color:{NAVY}">⏳ Your Time Doctor hours for yesterday are still coming in</div>'
            f'<div style="font-size:13px;color:{INK};margin-top:6px">Time Doctor sometimes uploads a little late. '
            f'We\'ll confirm your hours once they land — nothing for you to do.</div>'
            f'</div>')
    elif not tracking_ok:
        hero = (
            f'<div style="background:#FEF2F2;border:1px solid {RED};border-radius:12px;padding:16px">'
            f'<div style="font-size:15px;font-weight:800;color:{RED}">⚠️ We didn\'t see any Time Doctor tracking from you yesterday</div>'
            f'<div style="font-size:13px;color:{INK};margin-top:6px">If your Time Doctor isn\'t running or won\'t sign in, '
            f'it may be a technical issue. Please raise a quick IT ticket so we can fix it — you shouldn\'t lose credit for time you worked.</div>'
            f'<a href="{helpdesk_url}" style="display:inline-block;margin-top:10px;background:{ORANGE};color:{NAVY};'
            f'font-weight:700;font-size:14px;text-decoration:none;padding:11px 18px;border-radius:8px">🛠️ Create an IT ticket</a>'
            f'</div>')
    else:
        # Arrival / departure — a clear, labelled pair on the daily brief
        # (CFO: the start/finish from Time Doctor must be shown). Weekly wrap has
        # no single arrival time, so it is omitted there.
        times = ''
        if started and finished and not weekly:
            times = (
                f'<table role="presentation" style="width:100%;margin-top:10px;border-collapse:separate;border-spacing:8px 0"><tr>'
                f'<td style="width:50%;background:#fff;border:1px solid #EAEEF3;border-radius:10px;padding:10px 12px">'
                f'<div style="font-size:11px;color:{MUT};text-transform:uppercase;letter-spacing:.04em">🕗 Arrived</div>'
                f'<div style="font-size:18px;font-weight:800;color:{NAVY}">{started}</div></td>'
                f'<td style="width:50%;background:#fff;border:1px solid #EAEEF3;border-radius:10px;padding:10px 12px">'
                f'<div style="font-size:11px;color:{MUT};text-transform:uppercase;letter-spacing:.04em">🏁 Left</div>'
                f'<div style="font-size:18px;font-weight:800;color:{NAVY}">{finished}</div></td>'
                f'</tr></table>')
        # Productive hours — the key metric (time on apps rated productive in
        # Time Doctor). Shown right under the tracked total when we have it.
        prod_line = ''
        if productive_hours is not None:
            prod_line = (
                f'<div style="font-size:13px;color:{GREEN};font-weight:700;margin:2px 0 2px">'
                f'⚡ Time actually spent in work apps '
                f'<span style="color:{MUT};font-weight:600">— not time with the computer switched on</span></div>')
        hero = (
            f'<div style="background:#F8FAFC;border:1px solid #EAEEF3;border-radius:12px;padding:16px">'
            f'<div style="font-size:12px;color:{MUT};text-transform:uppercase;letter-spacing:.04em">{period_label}</div>'
            f'<div style="font-size:30px;font-weight:800;color:{NAVY};margin:2px 0">{headline}'
            f'<span style="font-size:15px;color:{MUT};font-weight:600"> / {required} h</span></div>'
            f'{prod_line}'
            f'<div style="height:9px;background:#E5EAF0;border-radius:6px;overflow:hidden;margin:8px 0">'
            f'<div style="height:9px;width:{pct}%;background:{st["color"]};border-radius:6px"></div></div>'
            f'<div style="font-size:14px;color:{st["color"]};font-weight:700">{st["emoji"]} {st["en"]} · '
            f'<span style="font-style:italic">{st["tsn"]}</span></div>{times}</div>')

    aria_block = ''
    if aria:
        aria_block = (
            f'<tr><td style="padding:12px 22px 0"><div class="ad-card" style="background:#F1F5FF;'
            f'border:1px solid #D8E2F5;border-radius:12px;padding:12px 14px">'
            f'<div style="font-size:11px;color:{NAVY};text-transform:uppercase;letter-spacing:.04em;font-weight:700">💬 A word from Aria</div>'
            f'<div style="font-size:13px;color:{INK};margin-top:4px;font-style:italic">{esc(aria)}</div></div></td></tr>')

    # Rotating "Did you know?" omni feature tip (different feature each day).
    tip_block = ''
    if tip:
        links = ''.join(
            f'<a href="{esc(url)}" style="display:inline-block;margin:6px 8px 0 0;padding:8px 14px;'
            f'background:{NAVY};color:{ORANGE};text-decoration:none;border-radius:8px;font-weight:700;'
            f'font-size:13px">{esc(label)}</a>'
            for label, url in tip.get('links', []))
        tip_block = (
            f'<tr><td style="padding:14px 22px 0"><div style="background:#FFF7E8;border:1px solid #F3E4C4;'
            f'border-radius:12px;padding:14px">'
            f'<div style="font-size:11px;color:{AMBER};text-transform:uppercase;letter-spacing:.04em;font-weight:700">'
            f'{tip.get("emoji","💡")} Did you know?</div>'
            f'<div style="font-size:15px;font-weight:800;color:{NAVY};margin-top:3px">{esc(tip.get("title",""))}</div>'
            f'<div style="font-size:13px;color:{INK};margin-top:4px">{esc(tip.get("blurb",""))}</div>'
            f'{links}</div></td></tr>')

    def section(title_emoji, title, inner):
        return (f'<tr><td style="padding:14px 22px 0">'
                f'<div style="font-size:13px;font-weight:700;color:{NAVY};letter-spacing:.02em">{title_emoji} {title}</div>'
                f'<div style="margin-top:6px">{inner}</div></td></tr>')

    task_html = ''.join(
        f'<div style="font-size:13px;color:{INK};padding:3px 0">• {esc(t["title"])} '
        f'<span style="color:{MUT};font-size:11px">({esc(t["priority"])})</span></div>' for t in tasks
    ) or f'<div style="font-size:13px;color:{MUT}">Nothing pending — go well. 🎉</div>'

    ann_html = ''.join(
        f'<div style="font-size:13px;color:{INK};padding:4px 0"><b>{esc(a["category"])}:</b> {esc(a["title"])}'
        + (f' — {esc(a["body"])}' if a.get("body") else '') + '</div>' for a in announcements
    )

    # Staff decisions waiting on this person. Payments are deliberately absent
    # (see PAYMENT_STREAMS above) - CFO 2026-09-12.
    pos_html = ''
    if approvals and approvals.get('count'):
        rows = ''
        for st in approvals['streams']:
            age = st['oldest_days']
            age_txt = (f' <span style="color:{MUT};font-size:11px">'
                       f'oldest {age} day{"" if age == 1 else "s"}</span>') if age else ''
            link = f'https://omni.alphadirect.co.bw{st["href"]}' if st['href'].startswith('/') else st['href']
            label = esc(st['label'])
            label = (f'<a href="{esc(link)}" style="color:{NAVY};text-decoration:none">{label}</a>'
                     if link else label)
            rows += (f'<div style="font-size:13px;color:{INK};padding:3px 0">• '
                     f'<b>{st["count"]}</b> — {label}{age_txt}</div>')
        rows += ('<div style="margin-top:8px">'
                 '<a href="https://omni.alphadirect.co.bw/app/approvals" '
                 f'style="color:{ORANGE};font-size:12px;font-weight:700;text-decoration:none">'
                 'Approve them on your phone &rarr;</a></div>')
        pos_html = section('🖊️', 'Waiting for your approval', rows)

    # Recruitment/regrade authorities awaiting this person's signature (CFO
    # 2026-08-12): folded here so the CFO no longer gets a separate email per
    # authority. Renders only for a signatory who has one outstanding.
    auth_html = ''
    if authorities:
        def _auth_row(a):
            head = (f'<div style="font-size:13px;color:{INK};padding:3px 0">• <b>{esc(a["reference"])}</b> — '
                    f'{esc(a["person_name"])} <span style="color:{MUT};font-size:11px">({esc(a["position"])})</span></div>')
            url = a.get('action_url')
            if url:
                # Login-free one-click sign, right from the brief (CFO 2026-08-18).
                head += (
                    f'<div style="padding:0 0 8px 12px">'
                    f'<a href="{esc(url)}" style="display:inline-block;background:#F4A623;color:#0D1B2A;'
                    f'text-decoration:none;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:700;margin-right:6px">&#10003; Approve</a>'
                    f'<a href="{esc(url)}#decline" style="display:inline-block;background:#DC2626;color:#fff;'
                    f'text-decoration:none;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:700">&#10007; Decline</a>'
                    f'</div>')
            return head
        rows = ''.join(_auth_row(a) for a in authorities)
        # Fall back to the omni page link only when no per-row button was minted.
        tail = ''
        if not any(a.get('action_url') for a in authorities):
            tail = ('<div style="margin-top:6px">'
                    '<a href="https://omni.alphadirect.co.bw/recruitment/authorities" '
                    f'style="color:{ORANGE};font-size:12px;font-weight:700;text-decoration:none">Open &amp; sign &rarr;</a></div>')
        auth_html = section('✍️', 'Recruitment signatures waiting on you', rows + tail)

    leave_html = f'<div style="font-size:13px;color:{INK}">{_leave_line(leave)}</div>'
    rank_line = (f'#{rank} of {team_size} this week' if rank else 'ranking soon')

    # ── Leaderboard block: yesterday's sprint hero + department league +
    # a gentle individual nudge. Everything here is already fair-gated by the
    # caller (people on leave or who pre-declared a client visit are excluded;
    # a broken tracker at 0h is never counted). Renders only what it is given.
    _lb_parts = []
    if yesterday_hero and yesterday_hero.get('name'):
        _yh = yesterday_hero
        _lb_parts.append(
            f'<td style="width:50%;padding-right:6px;vertical-align:top">'
            f'<div style="background:#FFF7ED;border:1px solid #FCD9A8;border-radius:12px;padding:12px">'
            f'<div style="font-size:11px;color:{AMBER};text-transform:uppercase">⚡ Yesterday\'s hero</div>'
            f'<div style="font-size:15px;font-weight:800;color:{NAVY}">{esc(_yh["name"])}</div>'
            f'<div style="font-size:12px;color:{MUT}">{_yh.get("hours", 0):.2f} h productive · the daily sprint 🔥</div>'
            f'</div></td>')
    if needs_boost and needs_boost.get('name'):
        _nb = needs_boost
        _lb_parts.append(
            f'<td style="width:50%;padding-left:6px;vertical-align:top">'
            f'<div style="background:#FEF7F7;border:1px solid #F3D6D6;border-radius:12px;padding:12px">'
            f'<div style="font-size:11px;color:#B45309;text-transform:uppercase">📉 Needs a boost</div>'
            f'<div style="font-size:15px;font-weight:800;color:{NAVY}">{esc(_nb["name"])}</div>'
            f'<div style="font-size:12px;color:{MUT}">{_nb.get("hours", 0):.2f} h productive · let\'s lift it today 💪</div>'
            f'</div></td>')
    _tiles = (f'<tr><td style="padding:8px 22px 0"><table role="presentation" style="width:100%"><tr>'
              f'{"".join(_lb_parts)}</tr></table></td></tr>') if _lb_parts else ''

    _league = ''
    if dept_league:
        _rows = []
        n = len(dept_league)
        for i, d in enumerate(dept_league):
            medal = '🥇' if i == 0 else '🥈' if i == 1 else '🥉' if i == 2 else str(i + 1)
            is_bottom = (i == n - 1 and n >= 2)
            bg = '#F0FDF4' if i == 0 else ('#FEF2F2' if is_bottom else ('#FAFAFA' if i % 2 else '#FFFFFF'))
            tag = ('leading' if i == 0 else 'trailing' if is_bottom else '—')
            tag_col = (GREEN if i == 0 else '#B42318' if is_bottom else MUT)
            hr_col = '#B42318' if is_bottom else NAVY
            if is_bottom:
                medal = '🐌'
            _rows.append(
                f'<tr style="background:{bg}">'
                f'<td style="padding:9px 14px;width:34px">{medal}</td>'
                f'<td style="padding:9px 6px;font-weight:{800 if (i == 0 or is_bottom) else 400}">{esc(d["name"])}</td>'
                f'<td style="padding:9px 14px;text-align:right;font-weight:700;color:{hr_col}">{d["avg"]:.1f} h</td>'
                f'<td style="padding:9px 14px;text-align:right;color:{tag_col}">{tag}</td></tr>')
        _bottom = dept_league[-1] if len(dept_league) >= 2 else None
        # Keep the shame line INSIDE the row's <td> — a bare <p> between <tr>s
        # gets hoisted out of the table by the mail client and floats to the top.
        _shame = (f'<p style="margin:8px 0 0;font-size:13px;color:{NAVY}">'
                  f'<b>{esc(_bottom["name"])} — bagaetsho, re a lekana?</b> Bottom of the league yesterday. '
                  f'Let\'s close the gap today. 🐾</p>') if _bottom else ''
        _league = (
            f'<tr><td style="padding:12px 22px 0">'
            f'<div style="border:1px solid #E4E7EC;border-radius:12px;overflow:hidden">'
            f'<div style="background:{NAVY};padding:10px 14px">'
            f'<span style="color:{AMBER};font-size:12px;font-weight:800">🏟️ DEPARTMENT LEAGUE — avg productive hrs / head (yesterday)</span></div>'
            f'<table role="presentation" style="width:100%;font-size:13px;color:{NAVY}">{"".join(_rows)}</table>'
            f'</div>{_shame}</td></tr>')
    leaderboard_block = _tiles + _league

    # Rule explainer — folded into the ONE morning brief (CFO 2026-07-22: staff
    # get a single Omni email, not a separate notice). Warm-up copy flips to
    # enforcement copy at 1 Sep 2026 via leave_accountability.enforcement_active.
    from hris import leave_accountability as _la
    _tgt = '4.5' if is_manager else '6.5'   # manager weekday rate (CFO 2026-07-23)
    if _la.enforcement_active(timezone.localdate()):
        _rule_body = (f'Omni tracks your productive hours each day — weekday target <b>{_tgt}h</b>, Saturday <b>3h</b>. '
                      'A day you are short with no accepted reason is applied as leave. If you disagree you can '
                      '<b>appeal</b> it for review. Explain a short day under <b>My Daily Brief</b>.')
    else:
        _rule_body = (f'Omni tracks your productive hours each day — weekday target <b>{_tgt}h</b>, Saturday <b>3h</b>. '
                      '<b>Right now is a warm-up: nothing is deducted.</b> From <b>1 September 2026</b>, a day you '
                      'are short with no accepted reason becomes leave — and you can <b>appeal</b> it before it is '
                      'finalised. Miss a day? Explain it under <b>My Daily Brief</b>.')
    policy_block = (
        f'<tr><td style="padding:14px 22px 0">'
        f'<div style="background:#FFF7E8;border:1px solid #F3E4C4;border-radius:12px;padding:13px 15px">'
        f'<div style="font-size:13px;font-weight:800;color:{NAVY}">📋 How this works</div>'
        f'<div style="font-size:12px;color:{INK};margin-top:3px">{_rule_body}</div></div></td></tr>')

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ margin:0; }}
  .ad-wrap {{ max-width:600px; margin:0 auto; }}
  .ad-gamerow td {{ display:table-cell; }}
  @media only screen and (max-width:480px) {{
    .ad-wrap {{ width:100% !important; }}
    .ad-gamerow, .ad-gamerow tbody, .ad-gamerow tr {{ display:block !important; width:100% !important; }}
    .ad-gamerow td {{ display:block !important; width:auto !important; padding:0 0 8px 0 !important; }}
    .ad-hero-num {{ font-size:26px !important; }}
    .ad-cta {{ display:block !important; text-align:center !important; }}
  }}
</style></head>
<body style="margin:0;background:#EEF2F7;font-family:{FONT}">
<div class="ad-wrap" style="max-width:600px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;margin-top:16px;margin-bottom:16px">
  <!-- Sunrise header -->
  <div style="background:linear-gradient(135deg,{NAVY} 0%,#243b55 60%,{ORANGE} 165%);padding:26px 22px">
    <div style="font-size:13px;color:#FFD98A;letter-spacing:.06em;text-transform:uppercase">🌅 Alpha Direct · Morning Brief</div>
    <div style="font-size:24px;font-weight:800;color:#fff;margin-top:6px">{opener(first, day)}</div>
    <div style="font-size:13px;color:#C7D2E0;margin-top:4px">{'Your week in review' if weekly else "Here's your day at a glance"} — {day.strftime('%A, %d %b %Y')}</div>
  </div>

  <table cellspacing="0" cellpadding="0" role="presentation" style="width:100%;border-collapse:collapse">
    <tr><td style="padding:20px 22px 6px" class="ad-hero-num">{hero}</td></tr>
    {aria_block}

    <!-- Gamification row -->
    <tr><td style="padding:10px 22px 0">
      <table class="ad-gamerow" role="presentation" style="width:100%"><tr>
        <td style="width:50%;padding-right:6px;vertical-align:top">
          <div style="background:#FFF7E8;border:1px solid #F3E4C4;border-radius:12px;padding:12px">
            <div style="font-size:11px;color:{AMBER};text-transform:uppercase">This week</div>
            <div style="font-size:18px;font-weight:800;color:{NAVY}">{week_hours} h</div>
            <div style="font-size:12px;color:{MUT}">{rank_line}{(' · ' + bdg) if bdg else ''}</div>
          </div>
        </td>
        <td style="width:50%;padding-left:6px;vertical-align:top">
          <div style="background:#ECFDF5;border:1px solid #C7EBD9;border-radius:12px;padding:12px">
            <div style="font-size:11px;color:{GREEN};text-transform:uppercase">🏆 Top performer — this week</div>
            <div style="font-size:15px;font-weight:800;color:{NAVY}">{winner_name or '—'}</div>
            <div style="font-size:12px;color:{MUT}">The marathon leader. Chase them! 💪</div>
          </div>
        </td>
      </tr></table>
    </td></tr>

    {leaderboard_block}

    {section('🌴', 'Your leave', leave_html)}
    {section('✅', 'Your tasks', task_html)}
    {auth_html}
    {(section('📌', 'Company &amp; HR matters', ann_html) if ann_html else '')}
    {pos_html}
    {tip_block}
    {policy_block}

    <tr><td style="padding:14px 22px 0">
      <div style="background:#EEF4FF;border:1px solid #CFE0F7;border-radius:12px;padding:13px 15px">
        <div style="font-size:13px;font-weight:800;color:{NAVY}">🗓️ Out of office soon?</div>
        <div style="font-size:12px;color:{INK};margin-top:3px">Golf day, client visit, leave or an off-site coming up? Tell us the day before — we'll log it, your manager sees the reason, and you won't be flagged for low hours.</div>
        <a class="ad-cta" href="{my_brief_url}" style="display:inline-block;margin-top:9px;background:{NAVY};color:#fff;font-weight:700;font-size:13px;text-decoration:none;padding:9px 16px;border-radius:8px">Tell us about an upcoming day →</a>
      </div>
    </td></tr>

    <tr><td style="padding:18px 22px 24px">
      <div style="border-top:1px solid #EEF2F7;padding-top:12px;font-size:12px;color:{MUT}">
        Have a great day — <span style="font-style:italic">Tlhola sentle!</span> 🌟<br>
        Hours from Time Doctor (totals only, no activity detail). Questions? Reply to your manager or HR.
      </div>
      <div style="margin-top:8px;font-size:11px;color:#8A93A3;line-height:1.5">
        Hours look wrong, or you were on leave? <a class="ad-cta" href="{bug_url}" style="color:{NAVY};font-weight:700;text-decoration:underline">Tap here to upload a screenshot to omni bug reporting</a> and we'll fix it.
      </div>
    </td></tr>
  </table>
</div></body></html>"""
