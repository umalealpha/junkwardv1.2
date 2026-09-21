"""
hris/management/commands/send_exceptions_report.py

Emails the manager "Daily Exceptions Report" (rich HTML) to the manager group
each morning. One company-wide pull → one report to all managers.

  python manage.py send_exceptions_report                 # yesterday, gated by switch
  python manage.py send_exceptions_report --dry-run
  python manage.py send_exceptions_report --preview-file out.html   # write + stop
  python manage.py send_exceptions_report --force

Recipients: settings.WORKFORCE_EXCEPTIONS_TO. Sent direct (not core.notifications)
so the _NEVER_CC blocklist doesn't strip the explicitly-named managers.
"""
from __future__ import annotations

import datetime
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris import exceptions_report

log = logging.getLogger(__name__)


def _house(title: str, body_html: str) -> str:
    """The house shell — navy header, orange title — so a failure notice still looks like us."""
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;background:#EEF2F7;font-family:\'Book Antiqua\',Palatino,Georgia,'
        'serif"><div style="max-width:640px;margin:16px auto;background:#fff;border-radius:14px;'
        'overflow:hidden"><div style="background:#0D1B2A;padding:18px 22px">'
        '<div style="font-size:11px;color:#FFD98A;letter-spacing:.06em;text-transform:uppercase">'
        'Alpha Direct &middot; Workforce</div>'
        f'<div style="font-size:18px;font-weight:800;color:#F4A623;margin-top:4px">{title}</div>'
        '</div><div style="padding:18px 22px;color:#1F2937;font-size:14px;line-height:1.65">'
        f'{body_html}</div></div></body></html>')


def _feedback_missing(today):
    """Managers who still owe last month's monthly performance feedback.

    Named for the C-suite (CFO 2026-07-20). Empty unless the performance module
    is enabled and we are a few days past month-end (grace before escalating)."""
    if not getattr(settings, 'ELRA_PERF_ENABLED', False):
        return []
    if today.day < 5:
        return []
    first = today.replace(day=1)
    last_prev = first - datetime.timedelta(days=1)
    year, month = last_prev.year, last_prev.month
    label = last_prev.strftime('%b %Y')
    from hris.models import HRISProfile
    from hris.performance_feedback_models import MonthlyCheckIn
    from payroll.models import Employee

    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    mgr_ids = (HRISProfile.objects.exclude(manager=None)
               .values_list('manager', flat=True).distinct())
    out = []
    for mgr in Employee.objects.filter(pk__in=list(mgr_ids)).exclude(is_test_record=True):
        reports = HRISProfile.objects.filter(manager=mgr).exclude(employee__is_test_record=True)
        total = reports.count()
        # DELIBERATE FORK of the "given" predicate: this is the CHASE list, and
        # after the 7th there is nothing left to chase — an auto-posted month is
        # done, just not by the manager. The delinquency is tracked in
        # manager_league / the scorecard, which DO exclude auto_posted. Do not
        # "align" these two without reading both (Fable round 2, 2026-08-27).
        given = MonthlyCheckIn.objects.filter(
            profile__in=reports, period_year=year, period_month=month).count()
        outstanding = total - given
        if outstanding > 0:
            # A NAMED, CLICKABLE line (CFO 2026-08-07: "give a link so he can
            # click and give feedback"). This email goes to a shared recipient
            # list, so the link is the ordinary in-app page pre-filtered to that
            # manager's team — never a signed one-click token, which would let
            # any reader record feedback as somebody else. Each manager gets
            # their own tokened link privately (monthly_feedback_cycle).
            out.append({
                'label': f'{mgr.full_name} — {outstanding} of {total} outstanding for {label}',
                'url': f'{base}/hris/monthly-feedback?manager={mgr.id}',
            })
    out.sort(key=lambda r: r['label'])
    return out


def _subject(day, data, weekly=False):
    """A subject line a manager can act on from the notification bar, with an
    emoji that says which kind of morning it is (CFO 2026-07-30 — "make this more
    fun, put an emoji so people can see it").

    The emoji is EARNED, not decoration: a clean day looks different from a day
    with no-shows, so the inbox itself becomes the scoreboard. Numbers quoted are
    productive hours — the same metric as the report body."""
    s = data.get('summary') or {}
    dnt = int(s.get('did_not_track') or 0)
    alarm = len(data.get('alarm') or [])
    short = len(data.get('shortfall') or [])
    groups = data.get('unexplained') or {}
    people = sum(len(v) for v in groups.values())
    mgrs = len([m for m in groups if m not in exceptions_report.NON_MANAGER_KEYS])
    prod = s.get('prod_h')
    up = (data.get('momentum') or {}).get('up')
    period = (f'week to {day.strftime("%d %b")}' if weekly else day.strftime('%a %d %b'))

    # Unexplained absence names the MANAGERS on the hook in the subject itself —
    # a manager should see they owe an answer without opening the mail (CFO
    # 2026-07-30). It outranks everything else because somebody must reply to it.
    if people:
        emoji = '🚨' if alarm else '🚩'
        head = f'{people} unexplained'
        if mgrs:
            head += f' · {mgrs} manager{"s" if mgrs != 1 else ""} must answer'
    elif alarm:
        # 3+ days dark but they self-reported today, so nothing is "unexplained" —
        # still the loudest thing in the report.
        emoji, head = '🚨', f'{alarm} on 3+ days of silence'
    elif dnt:
        emoji, head = '🚩', f'{dnt} did not track'
    elif short:
        emoji, head = '🐢', f'{short} short of target'
    elif up:
        emoji, head = '🏆', 'everyone hit their hours'
    else:
        emoji, head = '📊', 'all tracked'
    tail = f' · {prod}h productive' if prod not in (None, '') else ''
    kind = 'Weekly' if weekly else 'Time Doctor'
    return f'{emoji} {kind} — {head}{tail} ({period})'


class Command(BaseCommand):
    help = 'Email the manager group the Time Doctor Daily Exceptions report.'

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date')
        parser.add_argument('--weekly', action='store_true', help='Force weekly (Mon-Sat) mode.')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--preview-file', dest='preview_file')
        parser.add_argument('--ignore-breaker', action='store_true',
                            help='Send even if the data-sanity circuit breaker trips (debugging only).')

    def handle(self, *args, **opts):
        """Wrapped so a failure is never silent.

        Live on 2026-08-03: this crashed six times inside cron on an oversized Time Doctor
        response. Managers simply got no email, and nobody found out until the CFO asked.
        A report that fails quietly is worse than no report, because everyone assumes the
        silence means nobody was late.
        """
        try:
            self._run(*args, **opts)
        except Exception as exc:                      # noqa: BLE001 — the whole point
            self._report_the_failure(exc, opts)
            raise

    def _report_the_failure(self, exc, opts):
        """Tell the CFO the report did not go out, and why, in plain words."""
        import traceback
        detail = f'{type(exc).__name__}: {exc}'
        self.stderr.write(self.style.ERROR(f'REPORT FAILED — {detail}'))
        if opts.get('preview_file') or opts.get('dry_run'):
            return                                    # a dry run must not send mail
        body = (
            '<p style="margin:0 0 12px">This morning\'s manager report did <b>not</b> go out. '
            'Nobody received it, so no news today does not mean everybody was on time.</p>'
            f'<p style="margin:0 0 12px;font-size:13px"><b>What went wrong:</b><br>{detail[:400]}</p>'
            '<p style="margin:0 0 12px;font-size:13px">Most often this is Time Doctor returning '
            'a response too large to read. It usually works on the next run. If you see this '
            'two days running, it needs looking at.</p>'
            '<p style="margin:0;font-size:12px;color:#6B7280">Nothing was lost — the report can '
            'be re-run for the same day.</p>')
        try:
            from core.notifications import send_html_with_cfo_cc
            send_html_with_cfo_cc(
                subject='⚠️ The manager report did not go out this morning',
                html=_house('The manager report did not go out', body),
                to=['pganesharajah@alphadirect.co.bw'])
            self.stderr.write('Failure notice emailed to the CFO.')
        except Exception as send_exc:                 # noqa: BLE001
            # If even the alert cannot send, say so loudly in the log rather than swallowing it.
            self.stderr.write(self.style.ERROR(
                f'Could not email the failure notice either: {type(send_exc).__name__}'))
        log.error('send_exceptions_report failed: %s\n%s', detail, traceback.format_exc()[:2000])

    def _run(self, *args, **opts):
        from hris.models import WorkforceBriefSetting
        on = WorkforceBriefSetting.is_enabled() or getattr(settings, 'WORKFORCE_BRIEF_ENABLED', False)
        if not on and not opts.get('force') and not opts.get('preview_file'):
            self.stdout.write(self.style.WARNING('SKIPPED: Workforce switch is OFF (use --force to run).'))
            return

        from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
        client = TimeDoctorClient.from_settings()
        if not client.configured:
            self.stdout.write(self.style.WARNING('SKIPPED: TIMEDOCTOR_TOKEN not set.'))
            return

        from hris import workforce
        from hris.workforce_brief import holiday_off_dates
        off_dates = holiday_off_dates()
        today = timezone.localtime().date()
        base_day = (datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date() if opts.get('date')
                    else today - datetime.timedelta(days=1))
        # Decide daily-vs-weekly from the day being REPORTED ON, not the wall
        # clock. It used to read `today.weekday() == 6`, so re-running a
        # Wednesday report on a Sunday quietly produced a WEEKLY report for a
        # week nobody asked about — and the same wall-clock read made four tests
        # fail every weekend and pass again on Monday (2026-08-09). An explicit
        # --weekly still forces it.
        # No --date: the cron runs each morning and `today` decides. On a Sunday
        # run that yields the weekly Mon-Sat report, which is the long-standing
        # behaviour and is NOT changed here. Deliberately `today`, not base_day
        # (yesterday) — otherwise the Monday run would report on Sunday and flip
        # to weekly.
        report_day = base_day if opts.get('date') else today
        weekly = bool(opts.get('weekly')) or report_day.weekday() == 6   # Sunday

        # Persist matcher suggestions only on a real send — a dry-run must not
        # write to prod (feedback_never_write_test_on_prod).
        persist_map = not opts.get('dry_run') and not opts.get('preview_file')
        fb = _feedback_missing(today)
        try:
            if weekly:
                monday = base_day - datetime.timedelta(days=base_day.weekday())
                if today.weekday() == 6 and not opts.get('date'):
                    monday = today - datetime.timedelta(days=6)
                data = exceptions_report.compute_weekly(client, monday, off_dates, persist_map=persist_map)
                day = data['day']
            else:
                if workforce.required_hours_for_date(base_day, off_dates) <= 0 \
                        and not opts.get('force') and not opts.get('preview_file'):
                    self.stdout.write(self.style.SUCCESS(f'Rest day ({base_day}) — no exceptions report.'))
                    return
                day = base_day
                data = exceptions_report.compute(client, day, persist_map=persist_map,
                                                 off_dates=off_dates)
        except TimeDoctorError as exc:
            self.stderr.write(self.style.ERROR(f'Time Doctor pull failed: {exc}'))
            raise SystemExit(1)

        # Ghosts on payroll become HR tasks with a 3-day clock, and HR is named
        # here once that clock runs out (CFO 2026-07-30). Raising tasks WRITES, so
        # it happens on a real send only — a dry-run or preview must never create
        # work for HR. Reading the overdue list is safe everywhere, so the preview
        # still shows exactly what managers would see.
        from hris import ghost_payroll
        from django.conf import settings as _stg
        if persist_map:
            # 1) Anti-recurrence guard (CFO 2026-08-05): close ghost tasks for
            #    anyone now MATCHED or gone, so a recovered/working person is never
            #    re-shamed by a stale task — the incident that prompted all this.
            resolved = ghost_payroll.resolve_recovered_ghost_tasks(
                data.get('matched_names') or [], day,
                current_ghosts=data.get('ghosts') or [])
            if resolved:
                self.stdout.write(self.style.SUCCESS(
                    f'Ghost payroll: {len(resolved)} recovered task(s) auto-closed.'))
            # 2) DeepSeek + Gemini screen the remaining ghosts before HR is asked to
            #    act: a name the AI reads as a working person under an unlinked Time
            #    Doctor account is HELD from the chase task and gets a quiet
            #    "confirm the match" task instead. Fail-safe holds if it can't run.
            ghosts = list(data.get('ghosts') or [])
            ai_match_holds, ai_unavailable = [], []
            if ghosts and getattr(_stg, 'WORKFORCE_GHOST_AI_SCREEN', True):
                try:
                    ghosts, ai_match_holds, ai_unavailable = ghost_payroll.ai_screen_ghosts(
                        ghosts, data.get('unmatched_td') or [], day)
                except Exception as exc:    # noqa: BLE001 — a broken screen must FAIL SAFE, never chase
                    self.stderr.write(self.style.WARNING(
                        f'ghost AI screen errored — holding all ghosts this run: {exc}'))
                    ai_unavailable, ghosts = list(ghosts), []
            created, existing = ghost_payroll.raise_ghost_tasks(ghosts, day)
            data['ghost_tasks_new'] = created
            data['ghost_tasks_open'] = existing
            if created:
                self.stdout.write(self.style.SUCCESS(
                    f'Ghost payroll: {len(created)} HR task(s) raised, {existing} already open.'))
            if ai_match_holds:
                mapped = ghost_payroll.raise_mapping_tasks(ai_match_holds, day)
                self.stdout.write(self.style.SUCCESS(
                    f'Ghost payroll: {len(ai_match_holds)} likely-working name(s) held by the AI '
                    f'screen → {mapped} quiet match-confirm task(s), not shamed.'))
            if ai_unavailable:
                # Held only because the screen could not run — NOT chased and NOT
                # given a (false) match-confirm task; re-screened next run.
                self.stdout.write(self.style.WARNING(
                    f'Ghost payroll: {len(ai_unavailable)} name(s) HELD (AI screen could not run) — '
                    f'not chased, no task raised; will be re-screened next run.'))
        data['hr_overdue'] = ghost_payroll.hr_overdue(today)
        html = exceptions_report.build_html(
            data, weekly=weekly, feedback_missing=fb)

        if opts.get('preview_file'):
            with open(opts['preview_file'], 'w', encoding='utf-8') as fh:
                fh.write(html)
            self.stdout.write(self.style.SUCCESS(f'Wrote preview → {opts["preview_file"]}'))
            return

        recips = [a.strip() for a in (getattr(settings, 'WORKFORCE_EXCEPTIONS_TO', []) or []) if a and a.strip()]
        s = data['summary']
        tripped, why = exceptions_report.circuit_breaker(s)
        if opts.get('dry_run'):
            self.stdout.write(f"[dry] {day}: tracked={s['tracked']} dnt={s['did_not_track']} "
                              f"roster={s['roster']} alarm={len(data['alarm'])} "
                              f"ghosts={len(data['ghosts'])} -> {len(recips)} managers"
                              + (f' | BREAKER WOULD TRIP: {why}' if tripped else ' | breaker ok'))
            return
        if tripped and not opts.get('ignore_breaker'):
            self._alert_breaker(day, s, why, weekly)
            self.stderr.write(self.style.ERROR(f'BREAKER TRIPPED — report NOT sent to managers: {why}'))
            return
        # from_email is defined BEFORE the branch below so the late-correction
        # backstop can use it even when the main manager send is skipped.
        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        # Consolidated emails (CFO 2026-08-05): when ON, the scoreboard rides inside
        # the 07:05 Morning Brief for line managers who get a brief. The 07:10
        # manager-group send used to be skipped entirely in that case, but that
        # left the C-suite (WORKFORCE_EXCEPTIONS_TO — CFO, HR head, COO etc.)
        # relying on the brief's standalone-fallback path, which turned out to
        # miss low-hour cases (Manus QC, Tshephang Motswagae, CFO 2026-08-30 —
        # 10 straight unjustified days below 2.5h never reached management).
        # New rule: always send the standalone report to the manager group. A
        # small subset (line managers who also sit on WORKFORCE_EXCEPTIONS_TO)
        # may now receive a folded scoreboard AND a standalone — that is
        # deliberate belt-and-braces; the CFO would rather see it twice than
        # miss it once. OFF by default → unchanged.
        consolidated = getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False)
        if consolidated:
            self.stdout.write(self.style.SUCCESS(
                'CONSOLIDATED_EMAILS_ENABLED — Morning Brief folds the scoreboard '
                'for line managers; the standalone still goes to WORKFORCE_EXCEPTIONS_TO '
                'so C-suite oversight never misses a low-hours case.'))
        if not recips:
            self.stdout.write(self.style.WARNING('No WORKFORCE_EXCEPTIONS_TO recipients set.'))
            return
        else:
            try:
                msg = EmailMultiAlternatives(
                    subject=_subject(day, data, weekly),
                    body='(See the HTML version.)', from_email=from_email, to=recips)
                msg.attach_alternative(html, 'text/html')
                msg.send()
                self.stdout.write(self.style.SUCCESS(f'Exceptions report sent to {len(recips)} manager(s).'))
                held = data.get('held') or []
                ai_down = int(data.get('held_ai_down') or 0)
                if held:
                    style = self.style.WARNING if ai_down else self.style.SUCCESS
                    self.stdout.write(style(
                        f'Guardrail: {len(held)} name(s) withheld pending settle/verification'
                        + (f' — {ai_down} because the AI gate could NOT run (check the AI service)'
                           if ai_down else '')))
            except Exception as exc:    # noqa: BLE001
                self.stderr.write(self.style.WARNING(f'send failed: {exc}'))

        # Guardrail backstop (daily only): record who we published as did-not-track
        # today, then correct the PREVIOUS working day if anyone's hours arrived
        # late. Checked exactly once per day, the morning after it was reported.
        if not weekly:
            try:
                exceptions_report.persist_reported_no_track(
                    client, day, data.get('did_not_track_uids') or [])
                prev = day - datetime.timedelta(days=1)
                hops = 0
                while workforce.required_hours_for_date(prev, off_dates) <= 0 and hops < 6:
                    prev -= datetime.timedelta(days=1)
                    hops += 1
                late = exceptions_report.late_corrections(client, prev)
                if late:
                    EmailMultiAlternatives(
                        subject=f'✅ Correction — {len(late)} tracked after all ({prev.strftime("%a %d %b")})',
                        body='(See the HTML version.)', from_email=from_email, to=recips,
                        alternatives=[(exceptions_report.build_correction_html(prev, late), 'text/html')],
                    ).send()
                    self.stdout.write(self.style.SUCCESS(
                        f'Correction sent for {prev}: {", ".join(r["name"] for r in late)}.'))
            except Exception as exc:    # noqa: BLE001 — never let the backstop break the run
                self.stderr.write(self.style.WARNING(f'late-correction step skipped: {exc}'))

    def _alert_breaker(self, day, summary, why, weekly):
        """The breaker tripped — tell the CFO instead of the manager group."""
        to = [a.strip() for a in (getattr(settings, 'WORKFORCE_ALERT_TO', [])
                                  or ['pganesharajah@alphadirect.co.bw']) if a and a.strip()]
        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        kind = 'Weekly' if weekly else 'Daily'
        body = (f'The Time Doctor {kind} Exceptions report for {day} was NOT sent to managers.\n\n'
                f'Reason: {why}\n\n'
                f'Numbers seen: tracked={summary.get("tracked")}, '
                f'did_not_track={summary.get("did_not_track")}, roster={summary.get("roster")}.\n\n'
                'This is the data-sanity circuit breaker — it stops false "did not track" '
                'accusations when Time Doctor is down, the token expired, or matching broke. '
                'Check the token in the omni Vault and the Time Doctor service, then re-run.')
        try:
            EmailMultiAlternatives(
                subject=f'⚠️ Workforce report HELD — data looks wrong ({day})',
                body=body, from_email=from_email, to=to).send()
            self.stdout.write(self.style.WARNING(f'Breaker alert emailed to {", ".join(to)}.'))
        except Exception as exc:    # noqa: BLE001
            self.stderr.write(self.style.WARNING(f'breaker alert email failed: {exc}'))
