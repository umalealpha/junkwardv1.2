"""Manager Accountability Note (CFO 2026-07-25).

When a line manager has reports who tracked ZERO productive time for 3+
consecutive WORKING days AND gave no notice (no leave, no logged client
visit), the manager gets a firm, interactive note: the offenders, hard
questions, and a signed no-login link to respond. Their answer posts back to
Omni; silence past the deadline escalates — with the manager's name on it — to
Human Resources.

PEOPLE MATTERS ARE HR MATTERS (CFO 2026-08-03). This note is about named
employees, so it says Human Resources and it goes to Human Resources — never
"the CFO". The escalation that went out on 2 August told a manager his answer
would land on the CFO, which is both wrong and the kind of thing that makes a
people question feel like a finance investigation.

Only fires when there IS an unexplained gap, so a clean team hears nothing and
the note always means "act now".
"""
from __future__ import annotations

import datetime

from django.core import signing
from django.utils.html import escape as esc
from django.utils import timezone

SALT = 'mgr-accountability-v1'
MAX_AGE = 60 * 60 * 24 * 14          # signed link valid 14 days
MIN_DARK_DAYS = 3
LOOKBACK = 10                        # days to scan for the last tracked day

# CFO 2026-08-30 (Manus QC on Tshephang Motswagae): a person who tracks 10-30
# minutes per day is invisible to a zero-only "dark day" rule — every low day
# resets the streak because tracked_hours > 0 is true, so the 3-consecutive-
# dark-day note never forms. Count a working day as dark when tracked time is
# BELOW this threshold, not only when literally zero. 1.0h keeps the note aimed
# at genuine no-shows: someone with a legitimate half-day (attended a training,
# had a client visit) will normally have logged 3-6h that day and still passes.
DARK_HOURS_THRESHOLD = 1.0

NAVY, ORANGE, MUT, INK, RED = '#0D1B2A', '#F4A623', '#6B7280', '#1F2937', '#B42318'

# Silence escalates here, with the manager's name on it (CFO 2026-07-25).
# Arjun (arjuniyer@) removed on CFO instruction 2026-08-01 — off the manager
# emails entirely. He keeps every PERMISSION he had (portal, documents, the
# workforce switch); this is about what lands in his inbox.
#
# HR ONLY (CFO 2026-08-03). The note tells the manager this is a Human
# Resources matter, so the list must be Human Resources — an email that says
# "HR" while copying the CEO and the CFO is a lie to the person answering it.
# The CEO and the CFO came off for that reason, not because their oversight
# went away: both read the same answers in Omni whenever they want.
ESCALATION_EMAILS = [
    'ubutale@alphadirect.co.bw',        # CHCO — Unami Butale
    'dikgopoleng@alphadirect.co.bw',    # Human Capital — Dorothy Ikgopoleng
]

# When the leave register can't be trusted (below), escalation goes here only.
QUIET_ESCALATION_EMAILS = ['pganesharajah@alphadirect.co.bw']

# Absences never flagged and never escalated (CFO 2026-07-28: the CEO does not
# clock on Time Doctor, so his "dark" days are noise that lands on his own HR
# manager). Lower-case full names; override with settings.MA_EXEMPT_NAMES.
EXEMPT_NAMES = {'arun iyer', 'arun p. iyer', 'arun p iyer'}

# The leave register is only evidence of "no notice given" if staff actually use
# it. Below this many APPROVED leave rows per tracked head over the last 90 days
# we treat absence-without-a-record as unproven — see leave_register_reliable().
LEAVE_ROWS_PER_HEAD = 0.25
LEAVE_WINDOW_DAYS = 90


def exempt_names() -> set:
    from django.conf import settings
    extra = getattr(settings, 'MA_EXEMPT_NAMES', None) or []
    return {n.strip().lower() for n in (list(EXEMPT_NAMES) + list(extra)) if n and n.strip()}


def leave_register_reliable(on_date=None) -> bool:
    """True when Omni's leave register is used enough to be evidence.

    Oratile Ria Tlhomelang was escalated to management on 2026-07-28 for an
    absence she HAD taken leave for — she just had no LeaveRequest row (the
    whole table held 25 rows company-wide). Absence with no record proves
    nothing until the register is actually populated, so below the threshold we
    keep the manager note (it asks a fair question) but stop escalating past
    the CFO. Never raises: a broken lookup means "not reliable".
    """
    on_date = on_date or timezone.localdate()
    try:
        from hris import eligibility
        from hris.models import LeaveRequest

        heads = len(eligibility.tracking_profiles())
        if not heads:
            return False
        rows = (LeaveRequest.objects
                .filter(status=LeaveRequest.Status.APPROVED,
                        start_date__gte=on_date - datetime.timedelta(days=LEAVE_WINDOW_DAYS),
                        start_date__lte=on_date)
                .count())
        return rows >= heads * LEAVE_ROWS_PER_HEAD
    except Exception:    # noqa: BLE001
        return False


def make_token(note_id) -> str:
    return signing.dumps({'n': str(note_id)}, salt=SALT)


def read_token(token: str):
    try:
        return signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return None


def dark_working_streak(days: dict, secs: dict, label_day) -> tuple:
    """(consecutive dark WORKING days ending label_day, working days observed).

    `days` maps date -> WorkdayJustification-like object (`required_hours`,
    `tracked_hours`, `status`); `secs` maps date -> tracked seconds from Time
    Doctor. Days that required no work, and days we hold no record for, are
    SKIPPED — they neither count as dark nor break the streak. A day that was
    worked or already explained ends it.
    """
    dark = observed = 0
    for k in range(LOOKBACK):
        d = label_day - datetime.timedelta(days=k)
        j = days.get(d)
        if j is None:                                   # no record → unknown, never an accusation
            continue
        if float(getattr(j, 'required_hours', 0) or 0) <= 0:
            continue                                    # not a working day for this person
        observed += 1
        # 'justified' MUST be here (CFO 2026-08-03). It is the STRONGEST
        # explanation Omni holds — approved leave, an approved client visit, or an
        # explanation a manager has already signed off — yet it was missing while
        # the weaker 'explained' (self-reported, still awaiting that sign-off) was
        # present. So a day Omni had accepted as approved leave still counted as a
        # dark day. Oratile Ria Tlhomelang's five approved leave days (27-31 Jul)
        # sat exactly here; the only thing keeping her off a manager note was the
        # separate leave shield, which is just MIN_DARK_DAYS wide.
        #
        # "Worked" is measured against DARK_HOURS_THRESHOLD, not > 0, so a
        # dribble of 10-30 minutes/day does NOT reset the streak. Both the
        # WorkdayJustification's own tracked_hours and the raw Time Doctor
        # seconds are checked; whichever is larger wins, since some days the
        # justification row is written before the final TD sync completes.
        secs_hours = secs.get(d, 0) / 3600.0
        tracked = max(float(getattr(j, 'tracked_hours', 0) or 0), secs_hours)
        if (getattr(j, 'status', '') in ('met', 'justified', 'explained')
                or tracked >= DARK_HOURS_THRESHOLD):
            break                                       # worked or explained → streak ends
        dark += 1
    return dark, observed


def dark_reports_by_manager(client, label_day, min_days: int = MIN_DARK_DAYS):
    """{manager_Employee: [{name, last_tracked, days_dark}]} for reports dark
    `min_days`+ consecutive days ending label_day with no leave / client-visit
    shield. Reuses the morning-brief TD pull + matcher."""
    from integrations.td_matching import TDMatcher, active_td_users, collapse_users
    from hris import exceptions_report, eligibility
    from hris.models import ClientVisit, HRISProfile, LeaveRequest, WorkdayJustification

    users = collapse_users(active_td_users(client.users()))
    ids = [u.get('id') for u in users if u.get('id')]
    profiles = eligibility.tracking_profiles()
    matcher = TDMatcher(users, [p.employee for p in profiles])

    # Per-day tracked seconds for the last LOOKBACK days (one pull per day).
    day_sec: dict = {}
    for k in range(LOOKBACK):
        d = label_day - datetime.timedelta(days=k)
        f, t = exceptions_report.day_window_utc(d)
        pm = exceptions_report.per_user_day(users, client.worklog(f, t, user_ids=ids))
        for uid in matcher.employee_for_uid:
            day_sec.setdefault(uid, {})[d] = (pm.get(uid) or {'sec': 0})['sec']

    # Shield anyone who gave notice: leave OR a client visit in the window, plus
    # the standing exemptions. Leave counts across the WHOLE dark window and at
    # ANY status — a pending request is still notice, and leave that ended
    # mid-window used to leave the person exposed on the remaining days.
    win_start = label_day - datetime.timedelta(days=min_days)
    shielded = set(eligibility.on_leave_names(label_day)) | exempt_names()
    for lr in (LeaveRequest.objects
               .filter(start_date__lte=label_day, end_date__gte=win_start)
               .exclude(status__in=[LeaveRequest.Status.REFUSED,
                                    LeaveRequest.Status.CANCELLED])
               .select_related('profile__employee')):
        nm = (getattr(getattr(lr.profile, 'employee', None), 'full_name', '') or '').strip().lower()
        if nm:
            shielded.add(nm)
    for cv in (ClientVisit.objects
               .filter(visit_date__gte=win_start, visit_date__lte=label_day)
               .select_related('profile__employee')):
        nm = (getattr(getattr(cv.profile, 'employee', None), 'full_name', '') or '').strip().lower()
        if nm:
            shielded.add(nm)

    # Omni's own daily record of what each person actually OWED that day. A day
    # that required no work — a weekend, a public holiday, a rest day on a
    # Saturday rota — must never count as a dark day, and neither must a day we
    # hold no record for. Counting them is what manufactured Oratile Ria
    # Tlhomelang's "3 dark days" on 2026-07-28 out of Sat + Sun + Mon (she had
    # worked Wed/Thu/Fri over her hours) and flagged Gorata Taele off a single
    # day of data.
    workdays: dict = {}                                 # employee_id -> {date: WorkdayJustification}
    for j in (WorkdayJustification.objects
              .filter(work_date__gte=label_day - datetime.timedelta(days=LOOKBACK),
                      work_date__lte=label_day)
              .select_related('profile__employee')):
        emp_obj = getattr(j.profile, 'employee', None)
        if emp_obj is not None:
            workdays.setdefault(emp_obj.id, {})[j.work_date] = j

    # PEOPLE-DATA GUARDRAIL (CFO 2026-08-01): asking a manager why someone went
    # dark is an accusation too, so it gets the same settle + AI gate as the
    # 09:00 report and the morning brief. A held uid (hours still uploading) is
    # never handed to a manager. Fail-safe: unproven = held.
    # NOTE: this is a SECOND, independent filter — it decides whether we are allowed
    # to talk about a person at all. The workdays map above decides whether a day
    # counted as dark in the first place. Both are required: the loop below reads
    # `held` (line ~199) and `workdays` (line ~203). Dropping either reintroduces a
    # bug — losing `workdays` brings back the weekend false escalations, losing
    # `held` hands managers names whose hours were still uploading.
    from hris import people_data_guard as pdg
    dark_pairs = [(uid, (getattr(emp, 'full_name', '') or '').strip())
                  for uid, emp in matcher.employee_for_uid.items()
                  if (day_sec.get(uid, {}) or {}).get(label_day, 0) == 0
                  and (getattr(emp, 'full_name', '') or '').strip()]
    held = pdg.held_uids(dark_pairs, label_day, client=client) if dark_pairs else set()

    # FACTS gate (CFO 2026-08-03). The `shielded` set above only covers leave and
    # client visits inside a MIN_DARK_DAYS-wide window, but the streak below looks
    # back LOOKBACK days — so an explanation that fell out of that narrow window
    # stopped counting. And `workdays` carries the verdict STORED on the morning
    # of each day, which predates anything explained afterwards: leave captured on
    # return, a visit logged late. This is the check that was missing when Oratile
    # Ria Tlhomelang's name went out.
    #
    # A day Omni explains is marked explained in the in-memory map, so
    # dark_working_streak ends the streak on it exactly as it does for a worked
    # day. Built once per day for the whole roster — LOOKBACK x a few queries, not
    # per person.
    class _ExplainedDay:
        """Stand-in WorkdayJustification for a day Omni holds an explanation for."""
        __slots__ = ('required_hours', 'tracked_hours', 'status')

        def __init__(self, required):
            self.required_hours = required
            self.tracked_hours = 0
            self.status = 'justified'

    for k in range(LOOKBACK):
        d = label_day - datetime.timedelta(days=k)
        day_facts = pdg.facts_by_td_uid(matcher.employee_for_uid, d)
        if not day_facts:
            continue
        for uid, emp in matcher.employee_for_uid.items():
            emp_id = getattr(emp, 'id', None)
            row = workdays.get(emp_id, {}).get(d) if emp_id is not None else None
            if row is None:
                continue            # no record → the streak already skips it
            required = float(getattr(row, 'required_hours', 0) or 0)
            if required <= 0:
                continue            # a non-working day must stay SKIPPED, never
                                    # become a streak-BREAKER (that would clear
                                    # everyone off the first weekend it hit)
            ok, _why = pdg.facts_gate(day_facts.get(uid), d)
            if not ok:
                workdays[emp_id][d] = _ExplainedDay(required)

    out: dict = {}
    for uid, emp in matcher.employee_for_uid.items():
        name = (getattr(emp, 'full_name', '') or '').strip()
        if not name or name.lower() in shielded or uid in held:
            continue
        secs = day_sec.get(uid, {})
        dark, observed = dark_working_streak(
            workdays.get(getattr(emp, 'id', None), {}), secs, label_day)
        if dark < min_days or observed < min_days:      # thin data is not evidence
            continue
        last = None
        for k in range(LOOKBACK):
            d = label_day - datetime.timedelta(days=k)
            if secs.get(d, 0) > 0:
                last = d
                break
        prof = (HRISProfile.objects.filter(employee=emp)
                .select_related('manager').first())
        mgr = getattr(prof, 'manager', None) if prof else None
        if not mgr or not (getattr(mgr, 'email', '') or '').strip():
            continue                                    # can't hold a manager we can't reach
        out.setdefault(mgr, []).append({
            'name': name,
            # CFO 2026-08-30: the manager note offers three one-click mailto
            # actions (warn / apply leave / nudge). Each needs the employee's
            # own email; the id is kept for future deep-link support.
            'email': (getattr(emp, 'email', '') or '').strip(),
            'employee_id': str(getattr(emp, 'id', '') or ''),
            'last_tracked': last.strftime('%a %d %b') if last else 'over 10 days ago',
            'days_dark': dark,
        })
    return out


def _shell(title: str, inner: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            f'<body style="margin:0;background:#f3f4f6;">'
            f'<div style="font-family:Arial,Helvetica,sans-serif;color:{NAVY};max-width:680px;margin:0 auto;line-height:1.55;">'
            f'<div style="background:{NAVY};padding:16px 22px;border-radius:8px 8px 0 0;">'
            f'<span style="color:{ORANGE};font-size:17px;font-weight:bold;">{esc(title)}</span></div>'
            f'<div style="border:1px solid #e4e7ec;border-top:none;padding:22px;border-radius:0 0 8px 8px;background:#ffffff;">'
            f'{inner}</div>'
            f'<div style="text-align:center;color:#9ca3af;font-size:11px;padding:10px;">Alpha Direct Insurance — omni</div>'
            f'</div></body></html>')


def _quote_plus(s: str) -> str:
    """URL-encode for mailto subject/body — RFC 6068. Plus for space is fine
    in mail clients we care about; the '?', '&', '=' get %-encoded so they
    don't corrupt the mailto's own query separators."""
    from urllib.parse import quote
    return quote(s, safe='')


def _action_links(r: dict, hr_emails: list, cfo_name: str) -> str:
    """The three one-click controls per employee (CFO 2026-08-30). All three
    are mailto: URLs so they work with zero login and from any device — the
    CFO's mail client opens with the recipients, subject and body pre-filled;
    he taps send. No new Omni endpoints, no bearer tokens, no session needed.

      1. Warn — asks HR to prepare a warning letter, cc's the manager.
      2. Apply leave for him — asks HR to apply the leave.
      3. Nudge him — mails the employee directly to explain or apply leave.
    """
    name = r.get('name') or 'the employee'
    emp_email = r.get('email') or ''
    days = int(r.get('days_dark') or 0)
    last = r.get('last_tracked') or '—'
    hr_to = ','.join(e for e in hr_emails if e) or 'hr@alphadirect.co.bw'
    sig = f'\n\nRegards,\n{cfo_name}'
    # 1) Warning letter — to HR, cc the manager themselves for the audit trail
    warn_subj = f'Warning letter — {name} — {days} days below 1h'
    warn_body = (
        f'Human Resources,\n\n'
        f'Please prepare a warning letter for {name}. Time Doctor shows {days} '
        f'consecutive working days below 1 hour of tracked time (last tracked {last}). '
        f'No leave was applied and no client visit was logged.\n\n'
        f'Reason for the warning: unexcused absence / failure to track work.{sig}')
    # 2) Apply leave for him — again routed to HR to record on the roster
    leave_subj = f'Apply leave on behalf — {name} — {days} days'
    leave_body = (
        f'Human Resources,\n\n'
        f'Please apply leave for {name} to cover the {days} days of no tracking '
        f'(last tracked {last}). Leave type: [annual / sick / unpaid — please confirm '
        f'with the employee]. Once processed, please close out the Manager Note in Omni.{sig}')
    # 3) Nudge him — direct to the employee
    nudge_to = emp_email if emp_email else hr_to
    nudge_subj = f'Please apply for leave for the days you were away'
    nudge_body = (
        f'Hi {name.split()[0] if name.split() else ""},\n\n'
        f'Omni shows you tracked under 1 hour for {days} working days recently '
        f'(last tracked {last}). If you were on leave, please apply for it now on '
        f'https://omni.alphadirect.co.bw/hris/leave. If you were working, please explain '
        f'to me directly — same day.\n\nThank you.{sig}')
    def btn(label: str, href: str, bg: str, fg: str) -> str:
        return (f'<a href="{esc(href)}" style="display:inline-block;background:{bg};color:{fg};'
                f'text-decoration:none;font-size:11.5px;font-weight:700;padding:6px 10px;'
                f'border-radius:8px;margin:2px 4px 2px 0">{esc(label)}</a>')
    m1 = f'mailto:{hr_to}?subject={_quote_plus(warn_subj)}&body={_quote_plus(warn_body)}'
    m2 = f'mailto:{hr_to}?subject={_quote_plus(leave_subj)}&body={_quote_plus(leave_body)}'
    m3 = f'mailto:{nudge_to}?subject={_quote_plus(nudge_subj)}&body={_quote_plus(nudge_body)}'
    return (
        f'<div style="padding:6px 14px 10px 14px">'
        f'{btn("📝 Ask HR to send a warning", m1, NAVY, ORANGE)}'
        f'{btn("🏖 Apply leave for him", m2, "#0A9396", "#fff")}'
        f'{btn("🔔 Nudge him to apply leave", m3, "#6B7280", "#fff")}'
        f'</div>')


def build_manager_email(*, manager_name, reports, answer_url, deadline_str,
                        hr_emails=None, cfo_name='Prathap Ganesharajah') -> str:
    """The firm interactive note. `reports` = [{name,last_tracked,days_dark,email,employee_id}].

    Under each name we now offer three one-click mailto controls
    (warn / apply leave / nudge) so the manager can act without opening a
    second screen (CFO 2026-08-30 — the "basic control we have").
    """
    first = (manager_name or 'Manager').split()[0]
    hr_emails = list(hr_emails or ESCALATION_EMAILS)
    n = len(reports)
    rows = ''
    for i, r in enumerate(reports):
        bb = '' if i == len(reports) - 1 else 'border-bottom:1px solid #F3D6D6;'
        rows += (
            f'<tr style="{bb}"><td style="padding:9px 14px" colspan="3">'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px">'
            f'<b style="font-size:13px">{esc(r["name"])}</b>'
            f'<span style="font-size:12px;color:{MUT}">last tracked <b style="color:{NAVY}">{esc(r["last_tracked"])}</b></span>'
            f'<span style="color:{RED};font-size:12.5px"><b>{r["days_dark"]} days dark</b></span>'
            f'</div>'
            f'{_action_links(r, hr_emails, cfo_name)}'
            f'</td></tr>')
    plural = 'people have' if n != 1 else 'person has'
    inner = (
        f'<p style="margin:0 0 6px;font-size:15px;color:{NAVY}"><b>{esc(first)}</b> — {n} of your team '
        f'{plural} gone dark on Time Doctor and nobody told us why.</p>'

        f'<div style="border:1px solid #F3D6D6;background:#FEF7F7;border-radius:12px;overflow:hidden;margin:12px 0 14px">'
        f'<div style="background:{RED};padding:9px 14px"><span style="color:#fff;font-size:12px;font-weight:800">'
        f'⛔ YOUR TEAM — 3+ DAYS, NO HOURS, NO EXPLANATION</span></div>'
        f'<table role="presentation" style="width:100%;font-size:13px;color:{NAVY}">{rows}</table></div>'

        f'<div style="background:#FFF7E8;border:1px solid #F3E4C4;border-radius:12px;padding:14px 16px;margin:0 0 14px">'
        f'<div style="font-size:13px;font-weight:800;color:{NAVY};margin-bottom:6px">❓ Questions you need to answer — today</div>'
        f'<div style="font-size:13px;color:{NAVY};line-height:1.7">'
        f'1. {n} of your people recorded <b>below 1 hour</b> of productive time for 3+ days. Did you know? What have you done about it?<br>'
        f'2. No leave and no client visit was logged for any of them. Why not?<br>'
        f'3. If your team can run for three days with nobody recording work — <b>is the department over-staffed?</b> '
        f'Justify the headcount.</div></div>'

        f'<div style="border:1px solid #C7D6EA;background:#F5F9FF;border-radius:12px;padding:14px 16px">'
        f'<div style="font-size:13px;font-weight:800;color:{NAVY};margin-bottom:8px">✍️ Your response is required (it goes to Human Resources)</div>'
        f'<a href="{esc(answer_url)}" style="display:inline-block;background:{NAVY};color:{ORANGE};font-weight:800;'
        f'font-size:13px;text-decoration:none;padding:11px 18px;border-radius:10px">Answer now →</a>'
        f'<div style="font-size:12px;color:{MUT};margin-top:8px">One click, no login. If we hear nothing from you by '
        f'<b>{esc(deadline_str)}</b>, this escalates to Human Resources — with your name on it.</div></div>')
    return _shell("Your team's tracking — action needed", inner)


def build_escalation_email(*, manager_name, reports, sent_on, deadline_str) -> str:
    """Sent to the escalation net when a manager ignores the note."""
    n = len(reports)
    names = ', '.join(esc(r['name']) for r in reports)
    inner = (
        f'<p style="margin:0 0 8px;font-size:15px;color:{NAVY}"><b>{esc(manager_name)}</b> did not respond.</p>'
        f'<p style="margin:0 0 8px;font-size:13px;color:{INK}">A Manager Accountability Note was sent on '
        f'<b>{esc(str(sent_on))}</b> about <b>{n}</b> report(s) dark 3+ days with no leave or client visit logged: '
        f'{names}. The deadline (<b>{esc(deadline_str)}</b>) passed with no answer.</p>'
        f'<p style="margin:0;font-size:13px;color:{RED}"><b>Action:</b> the manager owes an explanation for the '
        f'unexplained downtime and the headcount. Silence on a direct accountability request is itself the finding.</p>')
    return _shell('Escalation — manager did not respond', inner)


def managers_on_the_hook(label_day=None, *, client=None, base_url=None) -> dict:
    """{mgr_email_lower: note_html} for every line manager who currently has an
    unexplained 3+ day team gap ending `label_day` (default: yesterday, matching
    send_manager_accountability's default).

    Factored out of send_manager_accountability.handle so the 06:30 task sweep can
    fold each manager's accountability note into their ONE 'morning to-dos' email
    when settings.CONSOLIDATED_EMAILS_ENABLED is on (CFO 2026-08-05), instead of a
    second standalone email. Flag OFF, send_manager_accountability still builds and
    sends the note itself exactly as before — this function is not on that path.

    For each manager it PERSISTS the ManagerAccountabilityNote (the row the
    answer-link token AND the escalate_manager_accountability sweep both depend on)
    and builds the interactive note via build_manager_email — so the section is
    byte-identical whether sent standalone or folded.

    Returns {} when Time Doctor is not configured or no manager is on the hook.
    A TimeDoctorError from the pull PROPAGATES to the caller (the sweep swallows
    it), so a TD outage never silently masks a real gap but also never blocks the
    task reminders it is folded into.
    """
    from django.conf import settings
    from django.utils import timezone as _tz

    from hris.models import ManagerAccountabilityNote
    from integrations.timedoctor import TimeDoctorClient

    if client is None:
        client = TimeDoctorClient.from_settings()
    if not client.configured:
        return {}

    if label_day is None:
        label_day = _tz.localtime().date() - datetime.timedelta(days=1)

    by_mgr = dark_reports_by_manager(client, label_day)
    if not by_mgr:
        return {}

    # Same deadline the standalone command sets: 17:00 local the next day.
    base_dt = _tz.make_aware(datetime.datetime.combine(
        label_day + datetime.timedelta(days=1), datetime.time(17, 0)))
    deadline_str = _tz.localtime(base_dt).strftime('%H:%M, %a %d %b')
    if base_url is None:
        base_url = getattr(settings, 'OMNI_BASE_URL', 'https://omni.alphadirect.co.bw')
    # The proxy only forwards /hris/api/* to Django — a bare /hris/... link 404s.
    answer_path = '/hris/api/manager-accountability/answer/'

    out: dict = {}
    for mgr, reports in by_mgr.items():
        mgr_email = (getattr(mgr, 'email', '') or '').strip()
        if not mgr_email:                               # can't reach them → skip
            continue
        mgr_name = (getattr(mgr, 'full_name', '') or '').strip()
        reports = sorted(reports, key=lambda r: r['days_dark'], reverse=True)
        note, _ = ManagerAccountabilityNote.objects.update_or_create(
            manager=mgr, for_date=label_day,
            defaults={'reports': reports, 'deadline': base_dt,
                      'response': '', 'responded_at': None, 'escalated_at': None})
        token = make_token(note.id)
        html = build_manager_email(
            manager_name=mgr_name, reports=reports,
            answer_url=f'{base_url}{answer_path}?t={token}',
            deadline_str=deadline_str)
        out[mgr_email.lower()] = html
    return out
