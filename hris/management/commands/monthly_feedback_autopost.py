"""
monthly_feedback_autopost — post the month's feedback when the manager stayed
silent (CFO 2026-08-26, run end of the 7th).

"Please confirm or you can input their feedback. Otherwise it will be
automatically given as feedback from your side itself. If managers don't respond
then that's it."

What it writes is a real MonthlyCheckIn so it lands on the same screen, in the
same history, as a manager's own — but:
  * rating = NOT_RATED. Omni states facts and does not judge. A machine-set
    rating would feed PIPs, warnings and flight-risk off data errors.
  * auto_posted = True, so every screen can say who wrote it and why.
  * The employee can still Accept / Partially accept / Decline, and can ask the
    manager for comments — that route is unchanged and is the fairness valve.

Only posts for a manager who was actually TOLD: a MonthlyFeedbackNotice of kind
NOTICE must exist for that manager and period. Nobody gets auto-feedback because
a cron misfired before the email went.

  python manage.py monthly_feedback_autopost --dry-run
  python manage.py monthly_feedback_autopost --commit
"""
from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


def _prev_month(today: dt.date) -> tuple[int, int]:
    first = today.replace(day=1)
    last_prev = first - dt.timedelta(days=1)
    return last_prev.year, last_prev.month


class Command(BaseCommand):
    help = 'Post unrated, fact-based feedback where the manager did not respond.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--year', type=int)
        parser.add_argument('--month', type=int)

    def handle(self, *args, **o):
        # The performance module's own gate (see performance_views.py — counsel/DPIA:
        # "No PII can flow before then"). Every sibling command carries it; these two
        # were the only entry points without it AND the only cron-enabled ones, so with
        # the flag off the 5th would mass-mail staff write-ups for a dormant module
        # whose one-click links all 403. Fable round 2, 2026-08-27.
        from django.conf import settings as _s
        if not getattr(_s, 'ELRA_PERF_ENABLED', False):
            self.stdout.write('ELRA_PERF_ENABLED is off — performance module dormant, '
                              'nothing sent.')
            return

        from hris.auto_feedback import team_facts
        from hris.performance_feedback_models import (
            MonthlyCheckIn, MonthlyFeedbackNotice as N, PerformanceCheckRating)

        today = timezone.localdate()
        year = o['year'] or _prev_month(today)[0]
        month = o['month'] or _prev_month(today)[1]
        commit = o['commit'] and not o['dry_run']

        # CFO 2026-08-27: auto-post only if BOTH the 5th draft AND the 6th
        # reminder actually sent. If our own mail failed, the manager gets more
        # time rather than being recorded as silent — and the wording below,
        # which tells the employee their manager was reminded twice, stays true.
        noticed = set(N.objects.filter(period_year=year, period_month=month,
                                       kind=N.Kind.NOTICE)
                      .values_list('manager_id', flat=True))
        reminded = set(N.objects.filter(period_year=year, period_month=month,
                                        kind=N.Kind.REMINDER)
                       .values_list('manager_id', flat=True))
        eligible = noticed & reminded
        skipped_no_reminder = len(noticed - reminded)
        # A reminder with no notice should be impossible; if it happens the
        # cycle ran out of order and staying silent about it hides a real fault.
        reminded_without_notice = len(reminded - noticed)
        told = N.objects.filter(period_year=year, period_month=month,
                                kind=N.Kind.NOTICE,
                                manager_id__in=eligible).select_related('manager')
        self.stdout.write(
            f'Period {year}-{month:02d} · {told.count()} manager(s) had BOTH the '
            f'draft and the reminder · {"COMMIT" if commit else "DRY RUN"}')
        if reminded_without_notice:
            self.stdout.write(self.style.WARNING(
                f'  {reminded_without_notice} manager(s) have a REMINDER but no draft '
                f'on record — the cycle ran out of order; not auto-posted.'))
        if skipped_no_reminder:
            self.stdout.write(self.style.WARNING(
                f'  {skipped_no_reminder} manager(s) got the draft but NOT the '
                f'reminder — held back on purpose, not auto-posted.'))

        posted = skipped = 0
        for notice in told:
            mgr = notice.manager
            if N.objects.filter(manager=mgr, period_year=year, period_month=month,
                                kind=N.Kind.AUTOPOST).exists():
                continue                                  # already auto-posted
            rows = [r for r in team_facts(mgr, year, month) if not r['already_done']]
            if not rows:
                skipped += 1
                continue

            self.stdout.write(f'  {mgr.full_name}: {len(rows)} to auto-post')
            for r in rows:
                self.stdout.write(f'     {r["name"]}')
                if not commit:
                    continue
                ci = self._post_one(r, mgr, year, month)
                posted += 1
                # OUTSIDE _post_one's transaction on purpose: a mail failure
                # must not roll back the check-in. Same trap as request_comments.
                self._tell_employee(ci)

            if commit:
                N.objects.create(manager=mgr, period_year=year, period_month=month,
                                 kind=N.Kind.AUTOPOST, outstanding=len(rows),
                                 team_size=len(team_facts(mgr, year, month)))
                self._tell_manager(mgr, rows, year, month)

        self.stdout.write(self.style.SUCCESS(
            f'{"posted" if commit else "would post"} {posted}, '
            f'managers with nothing outstanding {skipped}'))
        if not commit:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing written.'))

    @transaction.atomic
    def _post_one(self, row, mgr, year, month):
        from hris.performance_feedback_models import MonthlyCheckIn, PerformanceCheckRating
        from django.utils import timezone as tz

        ci = MonthlyCheckIn(
            profile=row['profile'],
            reviewer=None,                       # nobody reviewed it — that is the point
            period_month=month, period_year=year,
            conversation_date=tz.localdate(),
            overall_rating=PerformanceCheckRating.NOT_RATED,
            # The facts go in `evidence` — that is exactly what they are. There
            # is no `summary` field on this model (checked, not assumed).
            evidence=row['draft'],
            manager_comments=(
                'Posted by Omni. Your manager was sent this draft on the 5th and a '
                'reminder on the 6th, and did not confirm or change it. It is the '
                'facts on record for the month, with no rating.'),
            auto_posted=True,
            auto_posted_at=tz.now(),
        )
        ci.save()
        return ci

    def _tell_employee(self, ci):
        """Let the person know it is there and that they can push back.

        send_html_with_cfo_cc RAISES on a mail failure, and the old
        `except ImportError` did not catch that — one transient SMTP error on the
        7th would abort the whole company's autopost mid-loop, leaving earlier
        check-ins posted and later managers never processed until next month.
        The check-in is the record; the courtesy email must never kill the run.
        """
        import logging
        from hris.performance_feedback_notify import notify_employee_of_autopost
        try:
            notify_employee_of_autopost(ci)
        except Exception as exc:                                 # noqa: BLE001
            logging.getLogger(__name__).warning(
                'autopost: could not email %s about %s-%02d: %s',
                ci.profile.employee.full_name, ci.period_year, ci.period_month, exc)
            self.stdout.write(self.style.WARNING(
                f'     ! could not email {ci.profile.employee.full_name} — posted anyway'))

    def _tell_manager(self, mgr, rows, year, month):
        from core.notifications import send_html_with_cfo_cc
        from hris.performance_feedback_models import silent_months_for

        to = (getattr(mgr, 'email', '') or '').strip()
        if not to:
            return
        silent = silent_months_for(mgr)
        names = ''.join(f'<li>{r["name"]}</li>' for r in rows)
        extra = ('' if silent < 2 else
                 f'<p style="margin:0 0 12px;color:#D14343"><b>Omni has now posted '
                 f'your team\'s feedback for you in {silent} of the last six '
                 f'months.</b> It is on your own record.</p>')
        html = (f'<div style="font-family:Book Antiqua,Georgia,serif;font-size:14px;'
                f'line-height:1.6;color:#222;max-width:620px">'
                f'<p>{mgr.full_name.split()[0]}</p>'
                f'<p>The {year}-{month:02d} feedback for these people has been posted '
                f'by Omni, because nothing came back from you:</p><ul>{names}</ul>'
                f'{extra}'
                f'<p>No rating was given — Omni does not rate people. You can still '
                f'open any of them in Omni and add your own words.</p>'
                f'<p>Regards,<br><b>Omni</b></p></div>')
        try:
            send_html_with_cfo_cc(
                subject=f'{len(rows)} feedback(s) posted for you — {year}-{month:02d}',
                html=html, to=[to],
                text_fallback=f'{len(rows)} feedback(s) auto-posted for '
                              f'{year}-{month:02d}.',
                no_reply=False, allow_named_exec=True)
        except Exception as exc:                                 # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                'autopost: could not notify manager %s: %s', mgr.full_name, exc)
