"""
hris/ghost_payroll.py — ghosts on payroll become HR's task, with a clock on it
(CFO 2026-07-30: "ghost employees in payroll and not in time doctor, put a task
in omni for them, tell them to remove them from payroll if they are not working,
and 3 days after HR doesn't respond or fix, make fun of HR also").

A "ghost" is someone the payroll register pays who has no Time Doctor presence at
all. Two very different things produce that, and the task must not assume which:

  * the person genuinely is not working here any more — payroll is paying nobody, or
  * their tracker was never installed / never matched — the person is real and the
    DATA is the ghost.

So the task is an INVESTIGATION with a deadline, never an instruction to cut
somebody's pay. Removal from payroll and anything resembling a termination stay
CFO-authorised (standing rule) — HR confirms, the CFO authorises. What HR does not
get to do is nothing: after the deadline the unanswered task is named, with the HR
owner's name on it, in the same manager email everyone else is judged in.
"""
from __future__ import annotations

import datetime
import logging

log = logging.getLogger(__name__)

# Dorothy owns the ghost/exit-date tasks and is the one named if they go
# unanswered (CFO 2026-08-05: "Dorothy is the person"). Unami is the fallback.
HR_EMAILS = ('dikgopoleng@alphadirect.co.bw',    # Dorothy Ikgopoleng — Human Capital
             'ubutale@alphadirect.co.bw')        # Unami Butale — CHCO (fallback)
CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'
SOURCE = 'ghost_payroll'
MAPPING_SOURCE = 'ghost_mapping'                  # gentle "confirm & link" — never shamed
DEADLINE_DAYS = 3                                 # CFO: 3 days, then HR is named
DUE_TIME = datetime.time(16, 0)                   # house rule: tasks due 4pm


def _users():
    from django.contrib.auth.models import User
    hr = None
    for email in HR_EMAILS:
        hr = User.objects.filter(email__iexact=email).first()
        if hr:
            break
    cfo = User.objects.filter(email__iexact=CFO_EMAIL).first()
    assigner = cfo or User.objects.filter(is_superuser=True).order_by('id').first()
    return assigner, (hr or cfo or assigner)


def _title(name: str) -> str:
    return f'Ghost on payroll — no Time Doctor presence: {name}'


def raise_ghost_tasks(ghost_names, day):
    """One OPEN task per ghost, assigned to HR, due in DEADLINE_DAYS.

    Idempotent: an existing open task for the same person is left alone, so the
    daily report cannot spam HR with the same name every morning. Returns
    (created_names, skipped_existing_count)."""
    from core.models import OmniTask
    names = sorted({(n or '').strip() for n in (ghost_names or []) if (n or '').strip()})
    if not names:
        return [], 0
    assigner, assignee = _users()
    if assigner is None or assignee is None:
        return [], 0
    due = day + datetime.timedelta(days=DEADLINE_DAYS)
    created, skipped = [], 0
    open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]
    for name in names:
        title = _title(name)
        if OmniTask.objects.filter(source=SOURCE, title=title,
                                   status__in=open_states).exists():
            skipped += 1
            continue
        OmniTask.objects.create(
            assigner=assigner, assignee=assignee, title=title,
            body=(f'{name} is on the payroll register but has NO Time Doctor presence at all — '
                  f'no account matched, no hours on any day.\n\n'
                  f'ANSWER BY {due.strftime("%a %d %b %Y")} (16:00). One of two things is true:\n\n'
                  f'1. They no longer work here → give their EXACT LAST WORKING DAY (exit date) '
                  f'here and start the removal from payroll. Paying a person who does not work here '
                  f'is a cash leak every single month. The actual termination / payroll change is '
                  f'CFO-AUTHORISED — confirm the exit date here, do not action it alone.\n'
                  f'2. They do work here → then their tracker is the ghost, not the person. Get '
                  f'Time Doctor installed and matched to their payroll record, and say so here so '
                  f'they stop appearing on this list.\n\n'
                  f'Either way this task needs an answer, not a close. If it is still open after '
                  f'{DEADLINE_DAYS} days, HR is named in the daily workforce email that goes to '
                  f'every manager and the CFO.'),
            priority=OmniTask.Priority.HIGH, source=SOURCE,
            due_at=due, due_time=DUE_TIME,
            week_of=day - datetime.timedelta(days=day.weekday()))
        created.append(name)
    return created, skipped


def hr_overdue(today):
    """Ghost tasks HR has neither answered nor fixed past their deadline.

    [{'hr': owner name, 'person': ghost name, 'days_late': n, 'due': date}],
    worst first. This is what gets HR named in the email — the same treatment
    every manager gets for an unanswered question."""
    from core.models import OmniTask
    rows = []
    qs = (OmniTask.objects
          .filter(source=SOURCE,
                  status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS],
                  due_at__lt=today)
          .select_related('assignee'))
    for t in qs:
        u = t.assignee
        owner = ((f'{u.first_name} {u.last_name}'.strip() if u and (u.first_name or u.last_name)
                  else (getattr(u, 'username', '') or '')) or 'HR')
        person = t.title.split(':', 1)[1].strip() if ':' in t.title else t.title
        rows.append({'hr': owner, 'person': person, 'due': t.due_at,
                     'days_late': (today - t.due_at).days})
    rows.sort(key=lambda r: (-r['days_late'], r['person']))
    return rows


def _exempt_from_tracking() -> set:
    """Lower-cased names of active staff DELIBERATELY marked don't-track.

    Only `source == 'directive'` counts — a deliberate click by HR or the CFO.
    A 'no-payslip' person is NOT exempt; closing their task on that basis would
    hide a real payroll leak, which is the whole thing this guard protects.
    """
    try:
        from hris import eligibility
        return {(r.get('name') or '').strip().lower()
                for r in eligibility.tracking_roster()
                if not r.get('expected') and r.get('source') == 'directive'}
    except Exception:      # noqa: BLE001 — close FEWER tasks when unsure, never more
        log.debug('ghost_payroll: tracking roster unavailable', exc_info=True)
        return set()


def resolve_recovered_ghost_tasks(matched_names, day, author=None, current_ghosts=None):
    """Close open ghost tasks whose person is no longer a ghost — they now have a
    MATCHED Time Doctor account, or no active payroll record remains under that
    name. WRITES, so a caller runs it on a real send only. Returns the names closed.

    Anti-recurrence guard (CFO 2026-08-05). Before this, a ghost task raised while
    someone was unmatched lingered forever once they were matched or left, so a
    working person got re-named every morning — the whole incident that prompted
    this. Now the task self-closes the instant its reason is gone.

    Duplicate-name safety: pass `current_ghosts` (today's live ghost names) and a
    task is NEVER auto-closed while someone of that name is STILL a live ghost — so
    a matched twin can't silently close the real ghost's task (DeepSeek review). It
    only closes when the ghost condition provably no longer holds, and it only ever
    fires on a MATCHED (present, working) or no-active-record (already off payroll)
    person — neither of which hides a live payroll leak."""
    from core.models import OmniTask, OmniTaskComment
    from payroll.models import Employee
    from django.utils import timezone
    matched = {(n or '').strip().lower() for n in (matched_names or [])}
    exempt = _exempt_from_tracking()
    still_ghost = {(g or '').strip().lower() for g in (current_ghosts or [])}
    open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]
    if author is None:
        author, _assignee = _users()
    closed = []
    for t in OmniTask.objects.filter(source=SOURCE, status__in=open_states):
        name = t.title.split(':', 1)[1].strip() if ':' in t.title else t.title
        key = name.lower()
        if key in still_ghost:
            continue    # someone of this name is a live ghost today — never auto-close
        if key in matched:
            reason = ('now has a matched Time Doctor account (working, tracker linked) — '
                      'the earlier ghost flag was a stale false alarm')
        elif key in exempt:
            # CFO 2026-09-11 (Moemedi Mositiemang — a driver with no computer):
            # HR was still being named five days past deadline for a man nobody
            # expects to track. Once the don't-track directive is set there is
            # no question left to answer, so the task must not survive it.
            reason = ('no longer expected to track time — a deliberate '
                      "don't-track directive is on record, so there is nothing "
                      'left for HR to answer')
        elif (Employee.objects.filter(full_name__iexact=name).exists()
              and not Employee.objects.filter(full_name__iexact=name, status='active').exists()):
            reason = 'no active payroll record remains under this name'
        else:
            continue    # still a genuine ghost, or an orphan we cannot explain — leave it
        t.status = OmniTask.Status.CANCELLED
        t.completed_at = timezone.now()
        t.save(update_fields=['status', 'completed_at', 'updated_at'])
        if author is not None:
            OmniTaskComment.objects.create(
                task=t, author=author, new_status=OmniTask.Status.CANCELLED,
                body=f'Auto-resolved by the ghost-payroll guard: {reason}.')
        closed.append(name)
    if closed:
        # Count only — names are DPA-sensitive; the per-task OmniTaskComment holds
        # the who/why audit inside the access-controlled app (DeepSeek review).
        log.info('ghost_payroll: auto-closed %d recovered task(s)', len(closed))
    return closed


def ai_screen_ghosts(ghost_names, unmatched_td, day):
    """DeepSeek + Gemini cross-check BEFORE a ghost becomes an HR task: is this
    on-payroll name plausibly the same human as one of the Time Doctor accounts
    that matched nobody (a spelling / married-name / email variant)? If an engine
    says yes, HOLD the name — we do not send HR chasing someone who is actually
    working; it becomes a quiet 'confirm the Time Doctor match' note instead.

    Fail-safe: if the check cannot run (both engines down, or nothing safe to
    send), HOLD — never publish an unproven ghost (CFO 2026-08-05, "a deepseek
    guard so you won't mess up again"). Runs off the Claude subscription and behind
    core.ai_assist's PII firewall, same as the did-not-track guard.

    ghost_names: list[str]. unmatched_td: list[{'name','email'}] (TD accounts with
    no employee). Returns (report, matched_holds, unavailable_holds) — all lists of
    names: report = chase as ghosts; matched_holds = AI saw a real likeness, make a
    quiet match-confirm task; unavailable_holds = the screen could not run, so HOLD
    them — do NOT chase and do NOT claim a match (re-screened next run)."""
    names = [(g or '').strip() for g in (ghost_names or []) if (g or '').strip()]
    if not names:
        return [], [], []
    if not unmatched_td:
        # No unmatched Time Doctor account exists → a ghost genuinely has no
        # tracker to be confused with. Nothing for the AI to find; report all.
        return names, [], []
    try:
        from core.ai_assist import deepseek_complete, gemini_complete, is_safe_for_ai
    except Exception:   # noqa: BLE001 — app not importable → fail safe, hold (unavailable)
        return [], [], list(names)

    td_lines = '\n'.join(f'- {u.get("name", "?")} <{u.get("email", "") or ""}>' for u in unmatched_td)
    numbered = '\n'.join(f'[{i}] {g}' for i, g in enumerate(names))
    prompt = (
        'You are auditing a payroll-vs-time-tracking report before HR is asked to act. '
        'List (A) is people on payroll who matched NO Time Doctor account. List (B) is '
        'Time Doctor accounts that matched NO payroll person. For each payroll person [N] '
        'in (A), decide if they are LIKELY the same human as one of the (B) accounts — a '
        'spelling, married-name, or email variant — i.e. they ARE working, just unlinked. '
        'Be conservative: only say true on a genuine likeness. Reply ONLY as JSON keyed by '
        'the [N] number: {"verdicts":[{"id":N,"match":true|false}]}\n\n'
        f'(A) payroll people with no Time Doctor account:\n{numbered}\n\n'
        f'(B) Time Doctor accounts with no payroll person:\n{td_lines}\n'
    )
    safety = is_safe_for_ai(prompt)
    if not safety.safe or not safety.redacted_text:
        return [], [], list(names)          # nothing safe to send → HOLD (unavailable), never chase
    send_text = safety.redacted_text

    def _run(engine):
        try:
            import json
            data = json.loads(engine(send_text, response_format='json_object', max_tokens=800))
            out = {}
            for v in (data.get('verdicts') or []):
                try:
                    out[int(v.get('id'))] = bool(v.get('match'))
                except (TypeError, ValueError):
                    continue
            return out or None      # empty map = engine gave nothing usable = treat as down
        except Exception:   # noqa: BLE001
            return None

    ds, gm = _run(deepseek_complete), _run(gemini_complete)
    if ds is None and gm is None:
        return [], [], list(names)          # both engines down → HOLD all, never chase, no false match
    report, matched_holds = [], []
    for i, g in enumerate(names):
        if bool((ds or {}).get(i)) or bool((gm or {}).get(i)):
            matched_holds.append(g)         # AI sees a real likeness → quiet match-confirm task
        else:
            report.append(g)
    return report, matched_holds, []


def raise_mapping_tasks(names, day):
    """A gentle 'confirm & link their Time Doctor account' task for names the AI
    screen held (likely working under an unlinked TD account). Assigned to HR but
    NEVER named in the manager email — hr_overdue reads SOURCE only, so a mapping
    task cannot shame anyone. Idempotent by name. Returns the count created."""
    from core.models import OmniTask
    names = sorted({(n or '').strip() for n in (names or []) if (n or '').strip()})
    if not names:
        return 0
    assigner, assignee = _users()
    if assigner is None or assignee is None:
        return 0
    due = day + datetime.timedelta(days=DEADLINE_DAYS)
    open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]
    created = 0
    for name in names:
        title = f'Confirm Time Doctor match — {name}'
        if OmniTask.objects.filter(source=MAPPING_SOURCE, title=title,
                                   status__in=open_states).exists():
            continue
        OmniTask.objects.create(
            assigner=assigner, assignee=assignee, title=title,
            body=(f'{name} is on payroll with no linked Time Doctor account — but there IS an '
                  f'unlinked Time Doctor account that looks like the same person, so they are '
                  f'probably working, just not matched.\n\n'
                  f'Please open Who-tracks (/hris/tracking-setup), find {name}, and confirm the '
                  f'correct Time Doctor account so they match from tomorrow. If it turns out they '
                  f'are NOT that account, say so and they go back on the ghost list.'),
            priority=OmniTask.Priority.NORMAL, source=MAPPING_SOURCE,
            due_at=due, due_time=DUE_TIME,
            week_of=day - datetime.timedelta(days=day.weekday()))
        created += 1
    return created
