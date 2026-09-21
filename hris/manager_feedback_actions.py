"""
hris/manager_feedback_actions.py — one-click monthly performance feedback from
the morning manager email (CFO 2026-08-07).

Bharath tried to give one of his people feedback and could not get to it. The
morning exception email already names the managers who owe feedback; it now
carries a link straight to a sign-in-free page where the manager picks the
person, types three short boxes, chooses a rating, and saves. The record it
writes is the SAME hris.performance_feedback_models.MonthlyCheckIn the in-app
screen writes — no parallel store, no reconciliation later.

Security: identical to hris.leave_actions. The signed token binds
{manager_employee_id, period} and is the credential; GET has no side effect;
POST re-checks that the target is still a direct report of that manager.

Rating cap (CFO 2026-08-07): a person carrying tasks more than 2 days overdue
cannot be rated "Meets" or better. The choice list here is trimmed to match,
and hris.performance_views enforces the same rule server-side for the in-app
path, so the cap cannot be dodged by using the other screen.
"""
from __future__ import annotations


from django.conf import settings
from django.core import signing
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from hris.oneclick_page import page

_SALT = 'manager-feedback'
_MAX_AGE = 60 * 60 * 24 * 45  # 45 days — covers a full monthly cycle
_HEADER = 'Monthly Feedback'

MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July',
          'August', 'September', 'October', 'November', 'December']

RATINGS = [('EX', 'Exceeds expectations'), ('ME', 'Meets expectations'),
           ('PA', 'Partially meets'), ('BE', 'Below expectations'),
           ('SB', 'Significantly below')]
# Ratings still allowed when the person has long-overdue work.
CAPPED_RATINGS = [r for r in RATINGS if r[0] in ('PA', 'BE', 'SB')]


def make_token(manager_employee, year: int, month: int) -> str:
    return signing.dumps({'m': str(manager_employee.id), 'y': int(year), 'p': int(month)},
                         salt=_SALT)


def action_url(manager_employee, year: int, month: int) -> str:
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    return f'{base}/hris/api/manager-feedback/{make_token(manager_employee, year, month)}/'


def _load(token: str):
    """(manager_employee, year, month) or (None, reason_str, None)."""
    from payroll.models import Employee
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This feedback link has expired. Please use omni instead.', None
    except signing.BadSignature:
        return None, 'This feedback link is not valid.', None
    mgr = Employee.objects.filter(pk=data.get('m')).first()
    if mgr is None:
        return None, 'That manager record no longer exists.', None
    # The token lives 45 days. Someone who has left must not keep writing
    # performance records about staff on the strength of an old email
    # (DeepSeek review round 2, 2026-08-07). Re-checked on GET and on POST.
    if mgr.status == Employee.Status.TERMINATED:
        return None, 'This link is no longer active.', None
    mgr_user = getattr(mgr, 'user', None)
    if mgr_user is not None and not mgr_user.is_active:
        return None, 'This link is no longer active.', None
    return mgr, int(data.get('y')), int(data.get('p'))


def _reports(manager_employee):
    """Everyone this manager is responsible for — line-managed AND co-managed.

    co_manager was missing (2026-08-09). The in-app screen has always shown both,
    so the emailed one-click page quietly offered a manager FEWER of their own
    people than the app did — for the COO, one name instead of three. A shortcut
    into a feature must not show a different team from the feature itself.
    """
    from django.db.models import Q
    from hris.models import HRISProfile
    from payroll.models import Employee
    return list(HRISProfile.objects
                .select_related('employee')
                .filter(Q(manager=manager_employee) | Q(co_manager=manager_employee))
                .exclude(employee=None)
                .exclude(employee__status=Employee.Status.TERMINATED)
                .distinct()
                .order_by('employee__full_name'))


def _outstanding(manager_employee, year, month):
    """Direct reports with no check-in yet for the period."""
    from hris.performance_feedback_models import MonthlyCheckIn
    profiles = _reports(manager_employee)
    done = set(MonthlyCheckIn.objects
               .filter(profile__in=profiles, period_year=year, period_month=month)
               .values_list('profile_id', flat=True))
    return [p for p in profiles if p.pk not in done], len(profiles), len(done)


def _overdue_for(profile):
    """The person's long-overdue tasks, resolved through their login."""
    from hris import overdue_gate
    user = getattr(getattr(profile, 'employee', None), 'user', None)
    return overdue_gate.overdue_summary(user)


def _feature_off_page():
    if not getattr(settings, 'ELRA_PERF_ENABLED', False):
        return page(_HEADER, 'Not available',
                    '<h2>Not switched on yet</h2><p class="muted">The monthly performance '
                    'module is not enabled. Nothing to do here.</p>', status=403)
    return None


# ── views ────────────────────────────────────────────────────────────────────

@require_http_methods(['GET'])
def manager_feedback_page(request, token: str):
    """Side-effect-free. Safe for mail-scanner prefetch.

    Two screens on one URL (CFO 2026-08-07: "when I open this I should see the
    staff list, I should be able to click here and give feedback — we need to
    make performance coaching easy"):

      no ?profile=  → the TEAM LIST. One tappable row per person still owed
                      feedback, with a tick for the ones already done.
      ?profile=<id> → the form for that person, and Back returns to the list.

    A dropdown buried above a form made the manager hunt; a list they tap is
    the whole point.
    """
    off = _feature_off_page()
    if off:
        return off
    mgr, year, month = _load(token)
    if mgr is None:
        return page(_HEADER, 'Link problem',
                    f'<h2>Sorry</h2><p class="muted">{escape(year)}</p>', status=400)

    outstanding, total, done = _outstanding(mgr, year, month)
    period = f'{MONTHS[month]} {year}'
    if not outstanding:
        return page(_HEADER, 'All done', f"""
          <h2>All done ✓</h2>
          <p class="muted">You have given {period} feedback to all {total} of your people.
            Nothing outstanding.</p>""")

    target = (request.GET.get('profile') or '').strip()
    chosen = next((p for p in outstanding if str(p.pk) == target), None)

    # ── screen 1: the team list ──────────────────────────────────────────────
    if chosen is None:
        return page(_HEADER, 'Your team', _team_list_html(
            token, outstanding, period, total, done))

    # ── screen 2: one person's form ──────────────────────────────────────────
    ov = _overdue_for(chosen)
    ratings = CAPPED_RATINGS if ov['count'] else RATINGS
    rating_opts = ''.join(f'<option value="{c}">{escape(lbl)}</option>' for c, lbl in ratings)

    cap_note = ''
    prefill_concern = ''
    if ov['count']:
        from hris import overdue_gate
        prefill_concern = overdue_gate.plain_reason(ov)
        items = ''.join(f'<li>{escape(t["title"])} — {t["days_overdue"]} days late</li>'
                        for t in ov['tasks'][:5])
        cap_note = (f'<div class="warn"><b>{escape(chosen.employee.full_name)} has '
                    f'{ov["count"]} task(s) more than {ov["days_threshold"]} days overdue</b>'
                    f'<ul>{items}</ul>'
                    f'This is on the record automatically, and the rating cannot be '
                    f'higher than "Partially meets" this month.</div>')

    left = len(outstanding) - 1
    return page(_HEADER, chosen.employee.full_name, f"""
      <a href="/hris/api/manager-feedback/{escape(token)}/" class="back">← Back to my team</a>
      <h2>{escape(chosen.employee.full_name)}</h2>
      <p class="muted">{escape(period)} · {escape(getattr(chosen.employee, 'job_title', '') or 'Team member')}</p>
      {cap_note}
      <form method="POST" action="/hris/api/manager-feedback/{escape(token)}/submit/">
        <input type="hidden" name="profile" value="{escape(str(chosen.pk))}">
        <label class="fld">What they did well</label>
        <textarea name="strengths" rows="3" placeholder="One or two lines…"></textarea>
        <label class="fld">What did not go well</label>
        <textarea name="concerns" rows="3">{escape(prefill_concern)}</textarea>
        <label class="fld">What to improve, and the support you will give</label>
        <textarea name="support_provided" rows="3" placeholder="One or two lines…"></textarea>
        <label class="fld">Overall rating</label>
        <select name="overall_rating">{rating_opts}</select>
        <button class="btn btn-ok" type="submit">✓ Save and go back to my team</button>
      </form>
      <p class="muted" style="text-align:center;margin-top:10px">
        {left} other {'person' if left == 1 else 'people'} still to do.</p>""")


def _team_list_html(token, outstanding, period, total, done) -> str:
    """One tappable row per person. The whole row is the link — a phone thumb
    should not have to find a small word."""
    rows = ''
    for prof in outstanding:
        ov = _overdue_for(prof)
        flag = ('' if not ov['count'] else
                f'<span class="chip">{ov["count"]} overdue</span>')
        title = escape(getattr(prof.employee, 'job_title', '') or 'Team member')
        rows += (
            f'<a class="person" href="/hris/api/manager-feedback/{escape(token)}/'
            f'?profile={escape(str(prof.pk))}">'
            f'<span class="pname">{escape(prof.employee.full_name)}{flag}</span>'
            f'<span class="prole">{title}</span>'
            f'<span class="pgo">Give feedback →</span></a>'
            # CFO 2026-09-05 (Bharath: 6 of 13 on his list were not his): one tap
            # tells HR "this person doesn't report to me". The person STAYS on
            # the list until Unami or Dorothy moves them — same rule as the app.
            f'<form method="POST" action="/hris/api/manager-feedback/{escape(token)}/not-mine/" '
            f'class="notmine">'
            f'<input type="hidden" name="profile" value="{escape(str(prof.pk))}">'
            f'<button type="submit" class="notmine-btn" '
            f'title="Tell HR this person does not report to you">Not my report</button></form>')
    pct = round(100 * done / total) if total else 0
    return f"""
      <style>
        .notmine {{ margin:-6px 0 12px; text-align:right; }}
        .notmine-btn {{ background:none; border:1px solid #D1D5DB; color:#6B7280; border-radius:8px;
                       padding:6px 12px; font-size:12px; cursor:pointer; }}
        .notmine-btn:hover {{ border-color:#DC2626; color:#DC2626; }}
      </style>
      <h2>{escape(period)} feedback</h2>
      <p class="muted">{done} of {total} done. Tap a person to write two lines. No sign-in needed.
        Someone on this list who is not yours? Tap <b>Not my report</b> and HR will move them.</p>
      <div class="bar"><div class="barfill" style="width:{pct}%"></div></div>
      <div class="people">{rows}</div>"""

@csrf_exempt
@require_http_methods(['POST'])
def manager_feedback_submit(request, token: str):
    off = _feature_off_page()
    if off:
        return off
    mgr, year, month = _load(token)
    if mgr is None:
        return page(_HEADER, 'Link problem',
                    f'<h2>Sorry</h2><p class="muted">{escape(year)}</p>', status=400)

    from django.utils import timezone
    from hris.performance_feedback_models import MonthlyCheckIn

    outstanding, total, done = _outstanding(mgr, year, month)
    target = (request.POST.get('profile') or '').strip()
    chosen = next((p for p in outstanding if str(p.pk) == target), None)
    if chosen is None:
        return page(_HEADER, 'Already saved',
                    '<h2>Nothing to save</h2><p class="muted">That person already has '
                    'feedback for this month, or is no longer your direct report.</p>',
                    status=409)

    rating = (request.POST.get('overall_rating') or '').strip().upper()
    ov = _overdue_for(chosen)
    allowed = {c for c, _ in (CAPPED_RATINGS if ov['count'] else RATINGS)}
    if rating not in allowed:
        note = ('Because they have work more than '
                f'{ov["days_threshold"]} days overdue, the best available rating this month '
                'is "Partially meets".') if ov['count'] else 'Please choose a rating.'
        return page(_HEADER, 'Try again',
                    f'<h2>Rating not allowed</h2><p class="muted">{escape(note)}</p>', status=400)

    concerns = (request.POST.get('concerns') or '').strip()[:5000]
    # A Below / Significantly-below rating is only defensible with written
    # evidence (MonthlyCheckIn.clean, ELRA). The manager types one box on a
    # phone, so the concerns text doubles as the evidence rather than blocking
    # them with a second near-identical field.
    from hris.performance_feedback_models import LOW_RATINGS
    if rating in LOW_RATINGS and not concerns:
        return page(_HEADER, 'Try again',
                    '<h2>One more line needed</h2><p class="muted">A below-standard rating '
                    'has to say what went wrong. Please fill in "What did not go well".</p>',
                    status=400)

    # A double-tap on a phone would otherwise hit the (profile, month, year)
    # unique constraint and show a 500. One record wins, the second is told so
    # plainly (DeepSeek review round 2, 2026-08-07).
    from django.db import IntegrityError, transaction
    mgr_user = getattr(mgr, 'user', None)
    try:
        with transaction.atomic():
            MonthlyCheckIn.objects.create(
                profile=chosen,
                reviewer=mgr_user,
                period_year=year,
                period_month=month,
                conversation_date=timezone.localdate(),
                overall_rating=rating,
                strengths=(request.POST.get('strengths') or '').strip()[:5000],
                concerns=concerns,
                evidence=concerns if rating in LOW_RATINGS else '',
                support_provided=(request.POST.get('support_provided') or '').strip()[:5000],
            )
    except IntegrityError:
        return page(_HEADER, 'Already saved', f"""
          <h2>Already saved</h2>
          <p class="muted">{escape(MONTHS[month])} {year} feedback for
            <b>{escape(chosen.employee.full_name)}</b> is already recorded.
            Nothing was changed.</p>""", status=409)
    # That may have been the last person owed — close the task that chases this
    # manager now, rather than leaving it to tomorrow's sweep (bug 13869f41).
    from hris.feedback_task_close import close_if_complete
    close_if_complete(mgr, year, month)

    left = len(outstanding) - 1
    if left:
        nxt = (f'<a class="btn btn-ok" style="text-decoration:none;display:block" '
               f'href="/hris/api/manager-feedback/{escape(token)}/">'
               f'Next person ({left} left) →</a>')
    else:
        nxt = ('<p class="muted" style="text-align:center;margin-top:14px">'
               'That was the last one — your whole team is done. ✓</p>')
    return page(_HEADER, 'Saved', f"""
      <h2>✓ Saved</h2>
      <p class="muted">{escape(MONTHS[month])} {year} feedback recorded for
        <b>{escape(chosen.employee.full_name)}</b>. They will see it in omni and can reply.</p>
      {nxt}""")


@csrf_exempt
@require_http_methods(['POST'])
def manager_feedback_not_mine(request, token: str):
    """One tap from the emailed team list: "this person doesn't report to me".

    Writes the SAME RosterFlag the in-app roster writes (kind not_mine), raised
    by the manager's own login, and tells HR (Unami and Dorothy) through their
    dashboards. The person stays on the roster until HR decides — the manager's
    click is evidence, HR's action is the change (CFO 2026-07-26, 2026-09-05).
    """
    off = _feature_off_page()
    if off:
        return off
    mgr, year, month = _load(token)
    if mgr is None:
        return page(_HEADER, 'Link problem',
                    f'<h2>Sorry</h2><p class="muted">{escape(year)}</p>', status=400)
    from hris.roster_flag_models import FlagKind, FlagStatus, RosterFlag
    from hris.roster_flag_views import _notify_decider

    target = (request.POST.get('profile') or '').strip()
    chosen = next((p for p in _reports(mgr) if str(p.pk) == target), None)
    if chosen is None:
        return page(_HEADER, 'Not on your list',
                    '<h2>Nothing to flag</h2><p class="muted">That person is not on your '
                    'team list any more.</p>', status=409)
    raiser = getattr(mgr, 'user', None)
    note = (request.POST.get('note') or '').strip()[:1000]
    flag = RosterFlag.objects.filter(profile=chosen, kind=FlagKind.NOT_MY_REPORT,
                                     status=FlagStatus.OPEN,
                                     raised_by=raiser).first()
    if flag is None:
        flag = RosterFlag.objects.create(profile=chosen, raised_by=raiser,
                                         kind=FlagKind.NOT_MY_REPORT,
                                         note=note or f'Flagged from the {MONTHS[month]} {year} '
                                                      'feedback email.')
        _notify_decider(flag, raiser)
    back = f'/hris/api/manager-feedback/{escape(token)}/'
    return page(_HEADER, 'Sent to HR', f"""
      <h2>✓ Sent to HR</h2>
      <p class="muted"><b>{escape(chosen.employee.full_name)}</b> has been flagged as not
        reporting to you. Unami Butale or Dorothy Ikgopoleng will move them to the right
        manager. They stay on your list until HR does — you do not need to give them
        feedback this month.</p>
      <a class="btn btn-ok" style="text-decoration:none;display:block" href="{back}">← Back to my team</a>""")
