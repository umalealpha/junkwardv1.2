"""
hris/management/commands/send_morning_brief.py

The 6:50am Morning Brief. Pulls the whole team's Time Doctor hours, ranks them,
then emails each MATCHED employee their own warm, gamified brief.

  python manage.py send_morning_brief                 # yesterday, gated by switch
  python manage.py send_morning_brief --weekly        # Mon-Sat weekly wrap
  python manage.py send_morning_brief --dry-run
  python manage.py send_morning_brief --limit 3 --force
  python manage.py send_morning_brief --preview-file out.html --preview-name Prathap

Behaviour (Fable reviews 2026-07-14):
  - On a rest day (Sunday / non-working public holiday) the daily brief is
    SKIPPED entirely — no false "tracker broken" blast.
  - Runs in WEEKLY mode automatically on Sundays (covers the Mon-Sat just ended).
  - Identity comes from THE one shared matcher (integrations.td_matching):
    confirmed map rows → email → exact name → token-subset with a unique-hit
    guard. Nobody can be emailed another person's hours; unmatched employees
    are skipped — never sent a false IT-ticket warning.
  - "This week" is the REAL Mon-to-date total (a second pull), not yesterday
    relabelled; rank / top performer come from matched payroll staff only, so
    a contractor or role account can never be "Top performer".
  - Circuit breaker: if most matched staff show zero hours (TD outage / token
    death), NO briefs go out and the CFO is alerted instead.
Gated by the Workforce switch / WORKFORCE_BRIEF_ENABLED; --force bypasses.
Aggregates only, no activity titles. First-name personal.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris import morning_brief, workforce, omni_tips, leave_explain
from hris.workforce_brief import (leave_summary, pending_tasks, announcements_for,
                                   holiday_off_dates, outstanding_authorities_brief)
from hris.models import HRISProfile
from hris.workforce_roles import (NO_TRACKER_TITLES, is_manager_hours_profile,
                                  no_tracker_title)

log = logging.getLogger(__name__)


def _h(sec) -> Decimal:
    return Decimal(str(round((sec or 0) / 3600, 2)))


# Consolidated emails (CFO 2026-08-05): the visible boundary between a manager's
# own Morning Brief and the team "scoreboard" (the Daily/Weekly Exceptions report)
# folded in beneath it, so a manager gets ONE 07:05 email instead of two. Navy
# rule + Book-Antiqua heading, matching the house style of both emails.
_SCOREBOARD_SEP = (
    '<div style="max-width:680px;margin:26px auto 0;'
    'font-family:\'Book Antiqua\',\'Palatino Linotype\',Palatino,Georgia,serif">'
    '<hr style="border:none;border-top:2px solid #0D1B2A;margin:0 0 6px">'
    '<div style="font-size:16px;font-weight:800;color:#0D1B2A;padding:6px 22px 0">'
    '📋 Team scoreboard — Time Doctor</div>'
    '<div style="font-size:12px;color:#6B7280;padding:2px 22px 0">'
    'You manage a team, so your daily exceptions report is included below.</div></div>')


class Command(BaseCommand):
    help = 'Send each employee their gamified Morning Brief (daily, or weekly on Sundays).'

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date')
        parser.add_argument('--weekly', action='store_true', help='Force weekly (Mon-Sat) mode.')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--preview-file', dest='preview_file')
        parser.add_argument('--preview-name', dest='preview_name', default='')
        parser.add_argument('--ignore-breaker', action='store_true',
                            help='Send even if the data-sanity circuit breaker trips (debugging only).')

    def handle(self, *args, **opts):
        from hris.models import WorkforceBriefSetting
        on = WorkforceBriefSetting.is_enabled() or getattr(settings, 'WORKFORCE_BRIEF_ENABLED', False)
        if not on and not opts.get('force') and not opts.get('preview_file'):
            self.stdout.write(self.style.WARNING('SKIPPED: Morning Brief switch is OFF.'))
            return

        from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
        from integrations.td_matching import TDMatcher, active_td_users, collapse_users
        from hris import exceptions_report, eligibility
        client = TimeDoctorClient.from_settings()
        if not client.configured:
            self.stdout.write(self.style.WARNING('SKIPPED: TIMEDOCTOR_TOKEN not set.'))
            return

        today = timezone.localtime().date()
        # Daily-vs-weekly comes from the day being REPORTED ON, not the wall
        # clock — see the same fix in send_exceptions_report (2026-08-09).
        # Re-running a named date on a Sunday used to silently switch the whole
        # brief to the weekly format.
        _report_day = (datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
                       if opts.get('date') else today)
        weekly = bool(opts.get('weekly')) or _report_day.weekday() == 6   # Sunday
        off_dates = holiday_off_dates()

        if opts.get('date'):
            base_day = datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
        else:
            base_day = today - datetime.timedelta(days=1)

        monday = base_day - datetime.timedelta(days=base_day.weekday())
        if weekly:
            # The Mon-Sat that just ended (Sunday run) or the week containing base_day.
            if today.weekday() == 6 and not opts.get('date'):
                monday = today - datetime.timedelta(days=6)          # Mon before this Sunday
            required = sum((workforce.required_hours_for_date(monday + datetime.timedelta(days=i), off_dates)
                            for i in range(6)), Decimal('0'))
            label_day = monday + datetime.timedelta(days=5)          # show the Saturday date
            hero_from = exceptions_report.day_window_utc(monday)[0]
            hero_to = exceptions_report.day_window_utc(label_day)[1]           # Mon..Sat local
            week_from, week_to = hero_from, hero_to                             # hero IS the week
        else:
            required = workforce.required_hours_for_date(base_day, off_dates)
            if required <= 0:
                self.stdout.write(self.style.SUCCESS(f'Rest day ({base_day}) — no daily brief sent.'))
                return
            label_day = base_day
            hero_from, hero_to = exceptions_report.day_window_utc(base_day)    # the Botswana day
            # REAL Mon-to-date window for the "This week" tile + rank + badge
            # (the old code passed yesterday's hours as the week — Fable review).
            week_from = exceptions_report.day_window_utc(monday)[0]
            week_to = hero_to

        try:
            users_all = active_td_users(client.users())
            ids = [u.get('id') for u in users_all if u.get('id')]   # pull every machine
            users = collapse_users(users_all)                        # one row per person (machines summed)
            wl_hero = client.worklog(hero_from, hero_to, user_ids=ids)
            wl_week = wl_hero if weekly else client.worklog(week_from, week_to, user_ids=ids)
            tu_hero = client.timeuse(hero_from, hero_to, user_ids=ids)
        except TimeDoctorError as exc:
            # CFO 2026-09-18: on 17-Sep Time Doctor started answering 403 "denied"
            # and this branch exited silently, so the 09:00 brief simply never
            # arrived and nobody knew for 25 hours. Holding the staff brief is
            # still right — sending 95 people a zero-hours report off a dead feed
            # is worse than sending nothing — but the HOLD must now announce
            # itself, through the same alert the data-sanity breaker already uses.
            self.stderr.write(self.style.ERROR(f'Time Doctor pull failed: {exc}'))
            self._alert_breaker(label_day,
                                {'tracked': None, 'did_not_track': None, 'roster': None},
                                f'Time Doctor pull failed: {exc}', weekly)
            raise SystemExit(1)

        hero_map = exceptions_report.per_user_day(users, wl_hero)
        week_map = hero_map if weekly else exceptions_report.per_user_day(users, wl_week)
        # Productive hours for the hero window (the CFO's key metric — time on
        # apps rated productive in Time Doctor, scaled to stay ≤ tracked hours).
        from integrations.timedoctor import aggregate as _td_aggregate
        prod_by_uid = {m['user_id']: m.get('productive_hours')
                       for m in _td_aggregate(users, wl_hero, tu_hero, [], [],
                                              as_of=label_day, td_user_ids=ids)['members']}
        # Late starts this week per TD user (after 08:15 local — the single company
        # start time, exceptions_report.LATE_START_AFTER) — feeds the Aria
        # punctuality nudge ("third late start this week").
        late_by_uid = exceptions_report.late_days_by_user(wl_week)
        helpdesk_url = getattr(settings, 'HELPDESK_URL', 'https://omni.alphadirect.co.bw/helpdesk/')
        # One rotating omni feature tip for everyone today (different each day).
        tip = omni_tips.tip_for(label_day)

        # ONE matcher against the tracking-eligible payroll staff (agents /
        # role accounts / non-payroll excluded).
        on_leave = eligibility.on_leave_names(label_day)
        profiles = eligibility.tracking_profiles()
        # CEO / CFO are excluded by tracking_profiles() BEFORE this point — they
        # carry TrackingDirective(expected_to_track=False), which is correct: they
        # are genuinely not expected to track time. But "not tracked" was silently
        # being read as "gets no brief", and the CFO asked for the brief itself.
        # Add them back explicitly so the tracking_na path below can serve them.
        # Their uid is None, so no hours are claimed for them either way.
        _have = {p_.pk for p_ in profiles}
        for extra in (HRISProfile.objects
                      .select_related('employee')
                      .filter(employee__status='active')
                      .exclude(pk__in=_have)
                      .order_by('employee__full_name')):
            if no_tracker_title(extra) in NO_TRACKER_TITLES:
                profiles.append(extra)
        matcher = TDMatcher(users, [p.employee for p in profiles])
        if not opts.get('dry_run') and not opts.get('preview_file'):
            matcher.persist_suggestions()

        # Rank / winner pool: MATCHED payroll staff only, on week hours.
        week_sec = {uid: (week_map.get(uid) or {'sec': 0})['sec'] for uid in matcher.employee_for_uid}
        ranked = sorted([(uid, sec) for uid, sec in week_sec.items() if sec > 0],
                        key=lambda t: t[1], reverse=True)
        team_size = len(ranked)
        winner_name = ((getattr(matcher.employee_for_uid[ranked[0][0]], 'full_name', '') or '').strip()
                       if ranked else None)
        rank_of = {uid: i + 1 for i, (uid, _) in enumerate(ranked)}

        # ── Leaderboard: yesterday's sprint hero + department league + a gentle
        # individual nudge. SHIELD from any shame the people who told us in
        # advance: on approved leave, OR logged a client visit for the day (CFO
        # 2026-07-25 — never shame someone who gave notice). A 0h tracker (dead
        # token / genuine absence) is never counted as underperformance either.
        from hris.models import ClientVisit
        visiting = {(cv.profile.employee.full_name or '').strip().lower()
                    for cv in ClientVisit.objects.filter(visit_date=label_day)
                    .select_related('profile__employee')
                    if getattr(getattr(cv.profile, 'employee', None), 'full_name', '')}
        shielded = set(on_leave) | visiting

        def _name(uid):
            return (getattr(matcher.employee_for_uid[uid], 'full_name', '') or '').strip()

        def _hero_hours(uid):
            return (hero_map.get(uid) or {'sec': 0})['sec'] / 3600.0

        def _prod(uid):
            return float(prod_by_uid.get(uid) or 0)

        # Yesterday's hero — highest PRODUCTIVE hours on the day (CFO 2026-07-29).
        # It used to rank on the raw clock, which is how "21.64 h · the daily
        # sprint" reached all 79 staff. Positive callout, so no shield needed.
        _day_ranked = sorted(((uid, _prod(uid)) for uid in matcher.employee_for_uid),
                             key=lambda t: t[1], reverse=True)
        yesterday_hero = ({'name': _name(_day_ranked[0][0]), 'hours': _prod(_day_ranked[0][0])}
                          if _day_ranked and _day_ranked[0][1] > 0 else None)

        # Department league — avg productive hrs/head yesterday. Only people who
        # actually tracked (sec>0) and are not shielded count; a dept needs ≥3
        # such people to appear (so a tiny team isn't unfairly bottom).
        MIN_DEPT = 3
        _by_dept: dict = {}
        for uid, emp in matcher.employee_for_uid.items():
            if _name(uid).lower() in shielded:
                continue
            if (hero_map.get(uid) or {'sec': 0})['sec'] <= 0:      # broken/absent → not counted
                continue
            dept = (getattr(emp, 'department', '') or '').strip() or 'Unassigned'
            _by_dept.setdefault(dept, []).append(_prod(uid))
        dept_league = sorted(
            ({'name': d, 'avg': sum(v) / len(v)} for d, v in _by_dept.items() if len(v) >= MIN_DEPT),
            key=lambda x: x['avg'], reverse=True) or None

        # Needs a boost — gentle: the lowest productive hours among people who
        # DID track and are not shielded. NO exemption for C-suite/ExCo — they
        # are employees too and lead by example (CFO 2026-07-25). Never a 0h
        # (that's a tracker/absence issue, handled elsewhere — not public shame).
        # Double-check by EMPLOYEE PK (not just name) — name mismatches between
        # TD and Employee caused Laone Thebe to be flagged while on sick leave.
        _leave_pks = eligibility.on_leave_employee_pks(label_day)
        _boost_pool = [uid for uid in matcher.employee_for_uid
                       if _name(uid).lower() not in shielded
                       and matcher.employee_for_uid[uid].pk not in _leave_pks
                       and (hero_map.get(uid) or {'sec': 0})['sec'] > 0]
        needs_boost = None
        if len(_boost_pool) >= 5:      # only when there's a real field to rank against
            # Sort the pool by hours ascending so we can skip anyone flagged
            # by the DeepSeek/Gemini guard as a data error (Laone Thebe class).
            _sorted_pool = sorted(_boost_pool, key=_prod)
            _picked = None
            try:
                from hris.hours_reminder_guard import hold_suspect_reminders
                from integrations.models import TimeDoctorUserMap
                _confirmed_ids = set(TimeDoctorUserMap.objects.filter(
                    confirmed=True, employee__isnull=False
                ).values_list('employee_id', flat=True))
                for _candidate in _sorted_pool[:5]:
                    _emp = matcher.employee_for_uid[_candidate]
                    _item = [{
                        'ref': 0,
                        'hours': _prod(_candidate),
                        'threshold': 1.0,
                        'typical': None,
                        'mapped': _emp.pk in _confirmed_ids,
                        'arrival': None,
                    }]
                    _held, _why, _ran = hold_suspect_reminders(_item)
                    if 0 not in _held:
                        _picked = _candidate
                        break
                    log.info('boost: DeepSeek held %s (%s)', _name(_candidate),
                             _why.get(0, 'unknown'))
            except Exception:  # noqa: BLE001 — guard broke → fall back to PK-only check
                log.exception('boost: DeepSeek guard failed, falling back to first candidate')
                _picked = _sorted_pool[0] if _sorted_pool else None
            if _picked is None and _sorted_pool:
                _picked = _sorted_pool[0]
            if _picked is not None:
                needs_boost = {'name': _name(_picked), 'hours': _prod(_picked)}

        # Circuit breaker — a dead token / TD outage means most matched staff
        # show zero: do NOT blast "tracker broken" emails at the whole company.
        matched_active = [uid for uid, emp in matcher.employee_for_uid.items()
                          if (getattr(emp, 'full_name', '') or '').strip().lower() not in on_leave]
        zero_count = sum(1 for uid in matched_active if (hero_map.get(uid) or {'sec': 0})['sec'] == 0)
        summary = {'tracked': len(matched_active) - zero_count,
                   'did_not_track': zero_count, 'roster': len(matched_active)}
        tripped, why = exceptions_report.circuit_breaker(summary)
        if tripped and not opts.get('ignore_breaker') and not opts.get('preview_file'):
            if opts.get('dry_run'):
                self.stdout.write(self.style.WARNING(f'[dry] BREAKER WOULD TRIP: {why} '
                                                     f'(tracked={summary["tracked"]}/{summary["roster"]})'))
            else:
                self._alert_breaker(label_day, summary, why, weekly)
                self.stderr.write(self.style.ERROR(f'BREAKER TRIPPED — no briefs sent: {why}'))
                return

        # People-data guardrail (CFO 2026-08-01): never tell a person their
        # tracker is broken when their hours are just LATE. For matched staff who
        # show zero on the day, run the same settle + DeepSeek/Gemini check as the
        # manager report; HELD uids (unsettled or likely-late — the Keetile / Wangu
        # case) get a neutral "still coming in" note instead of the red IT-ticket
        # box. Daily only; the weekly wrap's "never tracked all week" is separate.
        held_uids: set = set()
        if not weekly and getattr(settings, 'WORKFORCE_DATA_GUARD_ENABLED', True):
            zero_pairs = [(uid, (getattr(emp, 'full_name', '') or '').strip())
                          for uid, emp in matcher.employee_for_uid.items()
                          if (hero_map.get(uid) or {'sec': 0})['sec'] == 0
                          and (getattr(emp, 'full_name', '') or '').strip().lower() not in on_leave]
            if zero_pairs:
                from hris import people_data_guard as pdg
                samples, hist = exceptions_report._guard_history_and_samples(client, label_day)
                cands = [{'uid': u, 'name': n, 'is_zero': True} for u, n in zero_pairs]
                try:
                    decisions = pdg.guard_candidates(
                        cands, samples, hist, label_day,
                        use_ai=getattr(settings, 'WORKFORCE_DATA_GUARD_AI', True))
                except Exception:    # noqa: BLE001
                    decisions = pdg.guard_candidates(cands, samples, hist, label_day, use_ai=False)
                held_uids = {u for u, d in decisions.items() if d.get('action') == 'hold'}

        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        from django.contrib.auth.models import User

        # ── Consolidated emails (CFO 2026-08-05): fold the manager team scoreboard
        # (the Daily/Weekly Exceptions report) INTO this 07:05 brief, so a manager
        # gets ONE email instead of two. Managers who are NOT tracked employees (or
        # otherwise get no brief) receive the standalone scoreboard as a fallback at
        # the end of this run — and send_exceptions_report skips its own manager send
        # when the flag is on, so nobody is emailed the scoreboard twice.
        #
        # OFF by default → none of this runs and the brief is byte-for-byte as before.
        # Never on dry-run/preview: those must stay read-only with no extra TD pull.
        consolidated = (getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False)
                        and not opts.get('dry_run') and not opts.get('preview_file'))
        scoreboard_html = None
        scoreboard_data = None
        # Exec oversight list - populated UNCONDITIONALLY (cheap settings read) so the
        # integrity alert reaches WORKFORCE_EXCEPTIONS_TO even when consolidated emails
        # are off (Fable 2026-09-01).
        mgr_group: set = {a.strip().lower() for a in
                          (getattr(settings, 'WORKFORCE_EXCEPTIONS_TO', []) or []) if a and a.strip()}
        folded_to: set = set()         # manager emails we folded the scoreboard into
        if consolidated:
            from hris.management.commands.send_exceptions_report import _feedback_missing
            try:
                # Compute ONCE, read-only (persist_map=False — the brief already
                # persists matcher suggestions above; do not double-write).
                if weekly:
                    scoreboard_data = exceptions_report.compute_weekly(
                        client, monday, off_dates, persist_map=False)
                else:
                    scoreboard_data = exceptions_report.compute(
                        client, base_day, persist_map=False, off_dates=off_dates)
                # Respect the exceptions circuit breaker on its OWN summary: a Time
                # Doctor outage must never fold a false "did not track" scoreboard
                # into anyone's brief. Personal briefs still go out — they are
                # governed by the brief's own breaker, checked earlier.
                tripped_sb, why_sb = exceptions_report.circuit_breaker(scoreboard_data['summary'])
                if tripped_sb:
                    scoreboard_data = None
                    self.stdout.write(self.style.WARNING(
                        f'Scoreboard breaker tripped ({why_sb}) — no team scoreboard folded/sent.'))
                else:
                    # HR-overdue naming is a pure READ; the ghost-task WRITES stay in
                    # send_exceptions_report so they run exactly once a day.
                    from hris import ghost_payroll
                    scoreboard_data['hr_overdue'] = ghost_payroll.hr_overdue(today)
                    scoreboard_html = exceptions_report.build_html(
                        scoreboard_data, weekly=weekly, feedback_missing=_feedback_missing(today),
                        show_tip=False)
            except Exception as exc:    # noqa: BLE001 — the scoreboard must never break the briefs
                scoreboard_html = scoreboard_data = None
                # With the flag ON, send_exceptions_report no longer sends the report,
                # so an unexpected compute failure here would silently drop the WHOLE
                # manager scoreboard for the day. Log it LOUD (ERROR → prod log /
                # nightly QC) rather than a quiet stderr line.
                log.exception('Consolidated team scoreboard compute failed (%s) — '
                              'no scoreboard folded or sent this run.', label_day)
                self.stderr.write(self.style.ERROR(f'team scoreboard skipped: {exc}'))

        # Time-tracking integrity alert (CFO 2026-09-01): the frozen-screen fraud
        # detector's flagged list for the day, computed ONCE for the whole company
        # and appended to every manager's brief below. morning_integrity_html never
        # raises (returns an amber 'could not run' box on any failure), so this can
        # never break the brief; skip the extra pull on a read-only dry-run.
        integrity_block = ''
        if not opts.get('dry_run') and not opts.get('preview_file'):
            try:
                from integrations import td_integrity_alert
                integrity_block = td_integrity_alert.morning_integrity_html(
                    base_day, client=client, users=users_all)
            except Exception:    # noqa: BLE001 -- belt-and-braces; must never break the brief
                log.exception("Integrity alert block failed - omitted from today's brief.")
                integrity_block = ''

        if opts.get('limit'):
            profiles = profiles[:int(opts['limit'])]

        sent = skipped = 0
        # Aria note is an external-AI call: memoise by a coarse bucket so ~80
        # employees collapse to a handful of calls, and only compute it for
        # people we will actually email/preview (Fable review 2026-07-14).
        aria_cache: dict = {}

        def aria_for(tracked, week_hours, rank, late_days, req):
            ratio = float(tracked) / float(req) if req else 0
            rb = 'hit' if ratio >= 1 else 'ok' if ratio >= 0.8 else 'low' if ratio >= 0.5 else 'vlow'
            rankb = 'top' if (rank or 999) <= 3 else 'mid' if (rank or 999) <= 20 else 'back'
            lateb = min(int(late_days), 3)
            # Managers have a different target, so the ratio bucket already
            # differs — key on the rounded target too so their note is cached apart.
            key = (weekly, rb, rankb, lateb, str(req))
            if key not in aria_cache:
                aria_cache[key] = morning_brief.aria_note(
                    required=req, tracked=tracked, week_hours=week_hours,
                    rank=rank, team_size=team_size, late_days=late_days, weekly=weekly)
            return aria_cache[key]

        for p in profiles:
            emp = getattr(p, 'employee', None)
            name = (getattr(emp, 'full_name', '') or '').strip()
            email = (getattr(emp, 'email', '') or '').strip()
            if opts.get('preview_file') and opts.get('preview_name') \
                    and opts['preview_name'].lower() not in name.lower():
                continue
            if name.lower() in on_leave:              # on approved leave → no brief
                skipped += 1
                continue
            uid = matcher.uid_for_employee_id.get(getattr(emp, 'id', None))
            # CEO / CFO get their brief even with no Time Doctor match (CFO
            # instruction, 11 Aug 2026). They do not run a tracker, so the match
            # was always going to be None and they were silently skipped — they
            # received no brief at all. `tracking_na` renders the hours card as
            # "not applicable to your role" instead of the red broken-tracker
            # warning, so the approvals / tasks / leave they actually want still
            # reach them. Everyone else with no match is still skipped: for a
            # tracked role a missing match means BROKEN DATA, and inventing a
            # zero for them would be the false IT-ticket warning this guard exists
            # to prevent.
            tracking_na = uid is None and no_tracker_title(p) in NO_TRACKER_TITLES
            if uid is None and not tracking_na:
                skipped += 1                          # unmatched → never send a false warning
                continue
            # Skip no-email staff BEFORE the AI call (nothing to send them).
            if not email and not opts.get('dry_run') and not opts.get('preview_file'):
                skipped += 1
                continue
            hero_rec = hero_map.get(uid) or {'sec': 0, 'start': None, 'end': None}
            tracked = _h(hero_rec['sec'])
            productive_hours = prod_by_uid.get(uid)
            week_hours = _h(week_sec.get(uid, 0))
            # A role with no tracker is neither OK nor broken — see tracking_na.
            tracking_ok = tracked > 0
            # Guardrail: an unproven zero (likely late data) is shown as PENDING,
            # never as a broken tracker. Weekly wrap is unaffected (held is empty).
            tracking_pending = uid in held_uids
            started = exceptions_report._hm(hero_rec['start']) if (tracking_ok and not weekly) else None
            finished = exceptions_report._hm(hero_rec['end']) if (tracking_ok and not weekly) else None
            user = User.objects.filter(email__iexact=email).first() if email else None
            late_days = int(late_by_uid.get(uid, 0))
            # Managers / EXCO / FM carry a lighter weekday requirement (CFO
            # 2026-07-23) — compute THIS person's target, not the team default.
            mgr = is_manager_hours_profile(p)
            if weekly:
                emp_required = workforce.weekly_required_hours(monday, off_dates, is_manager=mgr)
            else:
                emp_required = workforce.required_hours_for_date(base_day, off_dates, is_manager=mgr)
            emp_week_target = float(workforce.weekly_required_hours(monday, off_dates, is_manager=mgr, days=5))
            # No hours means no hours-coaching: ARIA's note is entirely about
            # tracked time, so for a no-tracker role it would invent commentary
            # on a figure that does not exist.
            aria = ('' if (opts.get('dry_run') or tracking_na)
                    else aria_for(tracked, week_hours, rank_of.get(uid), late_days, emp_required))
            ctx = dict(
                name=name, day=label_day, required=emp_required,
                tracked_yesterday=tracked, productive_hours=productive_hours, week_hours=week_hours,
                rank=rank_of.get(uid), team_size=team_size, winner_name=winner_name,
                yesterday_hero=yesterday_hero, dept_league=dept_league, needs_boost=needs_boost,
                leave=leave_summary(p, today), tasks=pending_tasks(p), announcements=announcements_for(p, today),
                authorities=outstanding_authorities_brief(p),
                approvals=morning_brief.people_approvals_for(user) if user else {'count': 0, 'streams': []},
                started=started, finished=finished, tracking_ok=tracking_ok,
                tracking_pending=tracking_pending, tracking_na=tracking_na, weekly=weekly,
                is_manager=mgr, week_target=emp_week_target,
                late_days=late_days, aria=aria, tip=tip, helpdesk_url=helpdesk_url,
            )
            # No-login "explain a day" link (golf/client/leave/off-site) — opens
            # the explain page directly instead of an omni login (CFO 2026-07-31).
            emp_id = getattr(emp, 'id', None)
            if emp_id is not None:
                ctx['my_brief_url'] = (
                    'https://omni.alphadirect.co.bw/api/leave-explain/'
                    f'{leave_explain.make_plan_token(emp_id)}/')
            html = morning_brief.build_morning_html(**ctx)

            if opts.get('preview_file'):
                with open(opts['preview_file'], 'w', encoding='utf-8') as fh:
                    fh.write(html)
                self.stdout.write(self.style.SUCCESS(f'Wrote preview for {name} → {opts["preview_file"]}'))
                return
            if opts.get('dry_run'):
                self.stdout.write(f'[dry] {name}: tracked={tracked}/{emp_required}h week={week_hours}h '
                                  f'{"MGR" if mgr else ""} ok={tracking_ok} rank={rank_of.get(uid)}/{team_size} weekly={weekly}')
                continue
            if not email:
                skipped += 1
                continue
            # Consolidated: this recipient is a team manager → fold the scoreboard
            # in beneath their personal brief. Recorded so the fallback below never
            # also emails them the standalone copy.
            send_html = html
            is_folded_mgr = bool(consolidated and scoreboard_html and email.lower() in mgr_group)
            if is_folded_mgr:
                send_html = html + _SCOREBOARD_SEP + scoreboard_html
            # Every manager sees the whole-company integrity alert (CFO 2026-09-01).
            if integrity_block and (mgr or email.lower() in mgr_group):
                send_html = send_html + integrity_block
            try:
                subj = (f'🌅 Your Week in Review — w/e {label_day.strftime("%d %b")}' if weekly
                        else f'🌅 Your Morning Brief — {label_day.strftime("%a %d %b")}')
                msg = EmailMultiAlternatives(subject=subj, body='(See the HTML version.)',
                                             from_email=from_email, to=[email])
                msg.attach_alternative(send_html, 'text/html')
                msg.send()
                sent += 1
                # Mark the scoreboard delivered ONLY after a successful send — else a
                # failed send would drop this manager from the standalone fallback
                # below and they'd receive NO scoreboard at all that day.
                if is_folded_mgr:
                    folded_to.add(email.lower())
            except Exception as exc:    # noqa: BLE001
                skipped += 1
                self.stderr.write(self.style.WARNING(f'email failed for {name}: {exc}'))

        # Standalone-fallback path REMOVED 2026-08-30 (Manus QC Hole 2, CFO):
        # send_exceptions_report now ALWAYS sends the standalone scoreboard to
        # WORKFORCE_EXCEPTIONS_TO whether consolidated is on or not, so C-suite
        # oversight never depends on a fold-in path that could miss a name.
        # Line managers still get the scoreboard folded into their personal
        # brief above; the belt-and-braces overlap for anyone in both lists is
        # deliberate.

        mode = 'WEEKLY' if weekly else 'daily'
        self.stdout.write(self.style.SUCCESS(
            f'{mode} brief {label_day}: emailed={sent}, skipped(unmatched/no-email/on-leave)={skipped}, '
            f'matched={len(matcher.employee_for_uid)}, ranked={team_size}.'))

    def _alert_breaker(self, day, summary, why, weekly):
        to = [a.strip() for a in (getattr(settings, 'WORKFORCE_ALERT_TO', [])
                                  or ['pganesharajah@alphadirect.co.bw']) if a and a.strip()]
        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        kind = 'weekly' if weekly else 'daily'
        body = (f'The {kind} Morning Brief for {day} was NOT sent to staff.\n\n'
                f'Reason: {why}\n\n'
                f'Numbers seen: tracked={summary.get("tracked")}, '
                f'zero-hours={summary.get("did_not_track")}, matched roster={summary.get("roster")}.\n\n'
                'This is the data-sanity circuit breaker — it stops a Time Doctor outage or an '
                'expired token from telling the whole company their tracker is broken. '
                'Check the token in the omni Vault and the Time Doctor service, then re-run.')
        try:
            EmailMultiAlternatives(
                subject=f'⚠️ Morning Brief HELD — data looks wrong ({day})',
                body=body, from_email=from_email, to=to).send()
            self.stdout.write(self.style.WARNING(f'Breaker alert emailed to {", ".join(to)}.'))
        except Exception as exc:    # noqa: BLE001
            self.stderr.write(self.style.WARNING(f'breaker alert email failed: {exc}'))
