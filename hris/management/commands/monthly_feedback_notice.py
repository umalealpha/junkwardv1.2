"""
monthly_feedback_notice — the 5th-of-month email to every manager who still owes
feedback, with the month already written up (CFO 2026-08-26).

ONE email per manager covering EVERYONE who reports to them: hours against
requirement, short days they never explained, work still outstanding, and a draft
paragraph per person. Three ways to act, all from a phone:
  confirm the draft · edit it · say nothing, and Omni posts it on the 7th.

Deliberately NOT a penalty notice (the CFO was explicit). It states facts and
carries no rating — see hris.auto_feedback.

Uses the EXISTING sign-in-free manager page (hris.manager_feedback_actions), so
the ten managers with no password can still act. That page already writes the
same MonthlyCheckIn as the in-app screen.

Schedule: --day-of notice on the 5th, --reminder on the 6th. Idempotent per
(manager, period, kind) via MonthlyFeedbackNotice, so a double cron run cannot
double-mail.

  python manage.py monthly_feedback_notice --dry-run
  python manage.py monthly_feedback_notice --commit
  python manage.py monthly_feedback_notice --commit --reminder
"""
from __future__ import annotations

import datetime as dt

from django.core.management.base import BaseCommand
from django.utils import timezone

NAVY, ORANGE, RED = '#0D1B2A', '#F4A623', '#D14343'
AUTOPOST_DAY = 10

# CFO 2026-09-09, the whole cycle in one place so no two files can disagree:
#   2nd  facts to the manager  ("the facts about the staff who report to you")
#   4th  reminder to the manager AND the staff member
#   7th  second reminder to both
#   10th auto-posted if nothing came back
NOTICE_DAY, REMINDER_DAY, REMINDER2_DAY = 2, 4, 7


def _prev_month(today: dt.date) -> tuple[int, int]:
    first = today.replace(day=1)
    last_prev = first - dt.timedelta(days=1)
    return last_prev.year, last_prev.month


class Command(BaseCommand):
    help = 'Email each manager the month\'s draft feedback for their whole team.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--final', action='store_true',
                            help='The 7th — the second and last reminder.')
        parser.add_argument('--reminder', action='store_true',
                            help='Wording for the 6th — last chance before it posts.')
        parser.add_argument('--year', type=int)
        parser.add_argument('--month', type=int)
        parser.add_argument('--only', default='', help='Limit to one manager email.')

    @staticmethod
    def _stage(o):
        """Which of the three sends this run is. --final wins over --reminder."""
        if o.get('final'):
            return 'final'
        return 'reminder' if o.get('reminder') else 'notice'

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
        from hris.manager_feedback_actions import action_url
        from hris.models import HRISProfile

        today = timezone.localdate()
        year = o['year'] or _prev_month(today)[0]
        month = o['month'] or _prev_month(today)[1]
        commit = o['commit'] and not o['dry_run']

        stage = self._stage(o)
        self._last_team = None
        managers = self._managers(o['only'])
        self.stdout.write(f'Period {year}-{month:02d} · {len(managers)} manager(s) '
                          f'· {"COMMIT" if commit else "DRY RUN"}')

        sent = skipped = nudged = 0
        for mgr in managers:
            rows = team_facts(mgr, year, month)
            outstanding = [r for r in rows if not r['already_done']]
            if not outstanding:
                skipped += 1
                continue
            to = (getattr(mgr, 'email', '') or '').strip()
            if not to:
                self.stdout.write(f'  ! {mgr.full_name}: no email — skipped')
                skipped += 1
                continue

            self._last_team = rows
            html = self._html(mgr, outstanding, rows, year, month,
                              action_url(mgr, year, month), stage)
            self.stdout.write(f'  {mgr.full_name}: {len(outstanding)} outstanding '
                              f'of {len(rows)} → {to}')
            if not commit:
                continue
            if self._send(to, mgr, outstanding, year, month, html, stage):
                sent += 1
                # CFO 2026-09-09: "4th reminder to staff and manager, 7th
                # another reminder to staff and manager". Telling only the
                # manager is why 83 write-ups were never signed — the person
                # being written about had no idea it was pending.
                if stage in ('reminder', 'final'):
                    nudged += self._tell_staff(outstanding, mgr, year, month, stage)

        self.stdout.write(self.style.SUCCESS(
            f'{"sent" if commit else "would send"} {sent}, skipped {skipped}'
            + (f', staff nudged {nudged}' if stage in ('reminder', 'final') else '')))
        if not commit:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing sent.'))

    # ---------------------------------------------------------------- #
    def _managers(self, only: str) -> list:
        """Everyone named as a manager or CO-manager on an HRIS profile.

        Both, because _reports() resolves both — a manager list that missed
        co-managed people would email a manager a team smaller than the one the
        page then shows them (that exact bug bit the COO on 2026-08-09).
        """
        from hris.models import HRISProfile
        from payroll.models import Employee
        from hris.manager_feedback_actions import _reports

        ids = set(HRISProfile.objects.exclude(manager=None)
                  .values_list('manager_id', flat=True))
        ids |= set(HRISProfile.objects.exclude(co_manager=None)
                   .values_list('co_manager_id', flat=True))
        qs = Employee.objects.filter(id__in=ids).exclude(
            status=Employee.Status.TERMINATED).order_by('full_name')
        out = []
        for mgr in qs:
            if only:
                em = getattr(mgr, 'email', '') or ''
                if only.lower() not in em.lower():
                    continue
            if _reports(mgr):
                out.append(mgr)
        return out

    def _html(self, mgr, outstanding, rows, year, month, url, stage) -> str:
        from calendar import month_name
        period = f'{month_name[month]} {year}'
        blocks = ''.join(self._person_block(r) for r in outstanding)
        done = len(rows) - len(outstanding)

        # CFO 2026-09-09, his words for the 2nd: "state that these are the facts
        # about the staff who report to you".
        lead = {
            'notice': (
                f'These are the facts about the staff who report to you for {period}, '
                f'taken from the working-time and task records. Confirm them, change '
                f'them, or add your own words. If nothing comes back by the '
                f'{AUTOPOST_DAY}th it is posted to your team as feedback from Omni.'),
            'reminder': (
                f'{period} feedback for your team is still outstanding. Your staff have '
                f'been told it is waiting on you. Anything not confirmed or edited by '
                f'the {AUTOPOST_DAY}th is posted to your team as feedback from Omni.'),
            'final': (
                f'This is the last reminder. Anything you have not confirmed or edited '
                f'by the {AUTOPOST_DAY}th will be posted to your team as feedback from '
                f'Omni, recorded as unrated because you did not respond.'),
        }[stage]

        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#f4f6f8">
<div style="max-width:680px;margin:0 auto;background:#fff">
  <div style="background:{NAVY};padding:20px 24px">
    <div style="color:#fff;font-family:Book Antiqua,Georgia,serif;font-size:11px;
                letter-spacing:2px;text-transform:uppercase;opacity:.7">Alpha Direct Insurance</div>
    <div style="color:{ORANGE};font-family:Book Antiqua,Georgia,serif;font-size:20px;
                font-weight:bold;margin-top:5px">{period} feedback for your team</div>
  </div>
  <div style="padding:22px 24px;font-family:Book Antiqua,Georgia,serif;font-size:14px;
              line-height:1.6;color:#222">
    <p style="margin:0 0 14px">{mgr.full_name.split()[0]}</p>
    <p style="margin:0 0 16px">{lead}</p>
    <p style="margin:0 0 18px">
      <a href="{url}" style="background:{ORANGE};color:{NAVY};text-decoration:none;
         font-weight:bold;padding:11px 20px;border-radius:8px;display:inline-block">
         Give the feedback now</a>
      <span style="display:block;color:#64748b;font-size:12px;margin-top:7px">
        No password needed — the link signs you in for this month only.</span>
    </p>
    <p style="margin:0 0 6px;color:#64748b;font-size:12px">
      {len(outstanding)} still to do{f' · {done} already done' if done else ''}</p>
    {blocks}
    <p style="margin:18px 0 0;color:#64748b;font-size:12px">
      These are facts from the working-time and task records, not an opinion.
      No rating has been set — that is yours to give.</p>
    <p style="margin:20px 0 0">Regards,<br><b>Omni</b><br>
      <span style="color:#64748b">Alpha Direct ERP</span></p>
  </div>
</div></body></html>"""

    def _person_block(self, r) -> str:
        f = r['facts']
        flags = []
        if f['short_days_unexplained']:
            flags.append(f'{f["short_days_unexplained"]} unexplained short day(s)')
        if f['overdue_count']:
            flags.append(f'{f["overdue_count"]} task(s) overdue')
        if f['hours_gap']:
            flags.append(f'{f["hours_gap"]:,.1f}h short')
        chips = ''.join(
            f'<span style="display:inline-block;background:#FBE9E9;color:{RED};'
            f'font-size:11px;padding:2px 9px;border-radius:999px;margin:0 5px 5px 0">'
            f'{c}</span>' for c in flags)
        body = (r['draft'] or '').replace('\n', '<br>')
        return f"""
    <div style="border:1px solid #e2e8f0;border-radius:10px;padding:13px;margin:0 0 11px">
      <div style="font-weight:bold;color:{NAVY};margin-bottom:5px">{r['name']}</div>
      {chips or ''}
      <div style="font-size:12.5px;color:#475569;line-height:1.55">{body}</div>
    </div>"""

    def _tell_staff(self, outstanding, mgr, year, month, stage) -> int:
        """Tell each person their own feedback is sitting with their manager.

        CFO 2026-09-09: the 4th and the 7th go to "staff and manager", not the
        manager alone. Before this, 83 write-ups carried a manager's name and
        were never signed, and the person being written about had no way to
        know one was pending — the only people chased were the ones who could
        already see the queue.

        Deliberately NOT a copy of the manager's email: that one carries the
        whole team's facts, and sending it to a team member would show them
        their colleagues' hours and shortfalls. This says only that theirs is
        waiting, and who it is waiting on.
        """
        from calendar import month_name
        from core.notifications import send_html_with_cfo_cc

        period = f'{month_name[month]} {year}'
        last = stage == 'final'
        n = 0
        for r in outstanding:
            to = ((getattr(getattr(r.get('profile'), 'employee', None), 'email', '')
                   or '')).strip()
            if not to:
                continue
            first = (r.get('name') or '').split()[0] if r.get('name') else 'Hello'
            html = f"""<div style="font-family:Book Antiqua,Georgia,serif;max-width:620px;
 margin:0 auto;color:#222">
  <div style="background:{NAVY};padding:18px 22px">
    <div style="color:{ORANGE};font-size:19px;font-weight:bold">Your {period} feedback</div>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:none;padding:20px 22px;font-size:14px;
              line-height:1.6">
    <p style="margin:0 0 14px">{first},</p>
    <p style="margin:0 0 14px">Your {period} feedback has not been completed yet. It is
      with <b>{mgr.full_name}</b>, who has been reminded
      {'again — this is the last reminder' if last else 'today'}.</p>
    <p style="margin:0 0 14px">If nothing comes back by the {AUTOPOST_DAY}th, Omni posts
      the month's facts as your feedback and you will be able to accept it or say you
      disagree.</p>
    <p style="margin:0 0 14px">Nothing is needed from you right now. This is so you know
      it is coming and who it is with.</p>
    <p style="margin:18px 0 0">Regards,<br><b>Omni</b><br>
      <span style="color:#64748b">Alpha Direct ERP</span></p>
  </div>
</div>"""
            try:
                # cc_cfo off: one line per person per month across 150 staff would
                # bury the EXCO inbox, and the manager's own email already reports
                # the same outstanding list in one message.
                if send_html_with_cfo_cc(
                        subject=f'Your {period} feedback is still with your manager',
                        html=html, to=[to],
                        text_fallback=(f'Your {period} feedback is still with '
                                       f'{mgr.full_name}. If nothing comes back by the '
                                       f'{AUTOPOST_DAY}th, Omni posts the month\'s facts '
                                       f'and you can accept or disagree.'),
                        cc_cfo=False, no_reply=False):
                    n += 1
            except Exception as exc:      # noqa: BLE001 — one bad address must not
                # abandon the rest of the company mid-loop (the 2026-08-27 lesson).
                self.stdout.write(f'      ! {to}: {type(exc).__name__} {exc}'[:160])
        return n

    def _send(self, to, mgr, outstanding, year, month, html, stage) -> bool:
        from core.notifications import send_html_with_cfo_cc
        from hris.performance_feedback_models import MonthlyFeedbackNotice as N

        kind = {'notice': N.Kind.NOTICE, 'reminder': N.Kind.REMINDER,
                'final': N.Kind.REMINDER2}[stage]
        if N.objects.filter(manager=mgr, period_year=year,
                            period_month=month, kind=kind).exists():
            self.stdout.write('    already sent for this period — skipped')
            return False

        n = len(outstanding)
        subject = {
            'notice':   f'{n} monthly feedback(s) to confirm for your team',
            'reminder': f'Reminder — {n} feedback(s) still to do for your team',
            'final':    f'Last chance — {n} feedback(s) post on the {AUTOPOST_DAY}th',
        }[stage]
        sent = send_html_with_cfo_cc(
            subject=subject, html=html, to=[to],
            text_fallback=(f'{n} of your team need {year}-{month:02d} feedback. '
                           f'Confirm or edit in Omni, or it posts on the '
                           f'{AUTOPOST_DAY}th as unrated feedback from Omni.'),
            no_reply=False, allow_named_exec=True,
        )
        if sent:
            # Send-then-stamp is the right order (never stamp a mail that failed),
            # but two crons can both pass the .exists() check above and both send.
            # The unique constraint stops a duplicate ROW; catching it here stops
            # the second run crashing mid-loop and abandoning the managers after
            # this one. Worst case is one duplicate email, which is survivable.
            from django.db import IntegrityError, transaction as _tx
            try:
                with _tx.atomic():
                    N.objects.create(manager=mgr, period_year=year,
                                     period_month=month, kind=kind, outstanding=n,
                                     sent_to=to[:200],
                                     team_size=len(self._last_team or []))
            except IntegrityError:
                self.stdout.write('    (another run stamped this period first)')
        return bool(sent)
