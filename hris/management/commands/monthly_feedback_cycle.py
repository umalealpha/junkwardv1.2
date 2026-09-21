"""
Monthly performance-feedback cycle (CFO 2026-07-20).

Run once a month (1st working day, for the month that just ended). Two jobs:
  1. Refresh each active target's actual for the period (auto-pull where possible)
     into PerformanceTargetResult, so the manager opens the prompt and the number
     is already there.
  2. Prompt every manager who still owes feedback: ONE HIGH OmniTask per manager
     with reports that have no MonthlyCheckIn for the period. The task auto-shows
     on their /tasks board AND their morning brief (no extra UI). Idempotent — a
     manager already prompted for the period is skipped.

Dry-run by default; pass --commit to write. --year/--month override the period.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

BASE = 'https://omni.alphadirect.co.bw'


def _prev_month(today: dt.date):
    first = today.replace(day=1)
    last_prev = first - dt.timedelta(days=1)
    return last_prev.year, last_prev.month


def _system_assigner():
    for email in ('pganesharajah@alphadirect.co.bw', 'ubutale@alphadirect.co.bw'):
        u = User.objects.filter(email__iexact=email).first()
        if u:
            return u
    return User.objects.filter(is_superuser=True).order_by('id').first()


class Command(BaseCommand):
    help = "Refresh monthly target actuals + prompt managers who owe feedback."

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Write (else dry-run).')
        parser.add_argument('--year', type=int)
        parser.add_argument('--month', type=int)

    def handle(self, *args, **opts):
        from hris.models import HRISProfile
        from hris.performance_feedback_models import MonthlyCheckIn
        from hris.performance_target_models import PerformanceTarget, PerformanceTargetResult
        from hris.perf_target_source import pull_actual, is_achieved
        from payroll.models import Employee
        from core.models import OmniTask

        from django.conf import settings
        if not getattr(settings, 'ELRA_PERF_ENABLED', False):
            self.stdout.write('ELRA performance module is off — nothing to do.')
            return

        commit = opts['commit']
        today = timezone.localdate()
        if opts.get('year') and opts.get('month'):
            year, month = int(opts['year']), int(opts['month'])
        else:
            year, month = _prev_month(today)
        tag = f'monthly_feedback:{year}-{month:02d}'
        self.stdout.write(f"Monthly feedback cycle for {year}-{month:02d} (commit={commit})")

        # ── 1. refresh target actuals (MONTHLY targets only) ─────────────────
        refreshed = 0
        for tgt in (PerformanceTarget.objects
                    .filter(active=True, cadence=PerformanceTarget.Cadence.MONTHLY)
                    .select_related('profile')):
            existing = PerformanceTargetResult.objects.filter(
                target=tgt, period_year=year, period_month=month).first()
            # NEVER overwrite a human-confirmed result — the manager's decision wins
            # over a re-run auto-pull (Fable H).
            if existing and existing.recorded_by_id:
                continue
            bd, source_used = pull_actual(tgt, year, month)
            if bd is None:
                # manual target — ensure a pending row exists so it shows on the prompt
                if commit and not existing:
                    PerformanceTargetResult.objects.create(
                        target=tgt, period_year=year, period_month=month,
                        profile=tgt.profile, target_value=tgt.target_value,
                        source_used='manual')
                continue
            excl = bd['excl']
            achieved = is_achieved(tgt, excl)
            if commit:
                PerformanceTargetResult.objects.update_or_create(
                    target=tgt, period_year=year, period_month=month,
                    defaults={'profile': tgt.profile, 'target_value': tgt.target_value,
                              'actual_value': excl, 'actual_excl': excl,
                              'actual_vat': bd['vat'], 'actual_incl': bd['incl'],
                              'achieved': achieved, 'source_used': source_used})
            refreshed += 1
        self.stdout.write(f"  auto-pulled actuals: {refreshed}")

        # ── 2. prompt managers who owe feedback ──────────────────────────────
        assigner = _system_assigner()
        mgr_ids = (HRISProfile.objects.exclude(manager=None)
                   .values_list('manager', flat=True).distinct())
        prompted = skipped = 0
        due = today + dt.timedelta(days=5)
        for mgr in Employee.objects.filter(pk__in=list(mgr_ids)).exclude(status=Employee.Status.TERMINATED):
            reports = list(HRISProfile.objects.filter(manager=mgr)
                           .exclude(employee__status=Employee.Status.TERMINATED)
                           .select_related('employee'))
            owed = [p for p in reports
                    if not MonthlyCheckIn.objects.filter(
                        profile=p, period_year=year, period_month=month).exists()]
            if not owed:
                continue
            mgr_user = mgr.user or (User.objects.filter(email__iexact=(mgr.email or '')).first()
                                    if mgr.email else None)
            if mgr_user is None:
                self.stdout.write(f"  ! {mgr.full_name}: no login, cannot prompt")
                continue
            if OmniTask.objects.filter(assignee=mgr_user, source=tag).exists():
                skipped += 1
                continue
            # One-click, sign-in-free link (CFO 2026-08-07). Bharath could not
            # get to the in-app screen at all; this lets a manager finish the
            # whole thing from their phone. The token is PRIVATE to this manager
            # — it goes in their own task and their own email, never into the
            # shared morning exceptions email.
            from hris.manager_feedback_actions import action_url
            one_click = action_url(mgr, year, month)
            title = f"Monthly performance feedback — {len(owed)} team member(s) for {year}-{month:02d}"
            # The link goes FIRST and each person gets their own tappable line
            # (CFO 2026-08-07: "when I open this I should see the staff list, I
            # should be able to click here and give feedback"). A wall of names
            # above a bare URL made the manager hunt for the way in.
            per_person = "\n".join(
                f"- {p.employee.full_name}: {one_click}?profile={p.pk}"
                for p in owed if p.employee)
            body = (f"Tap a name to write two lines about them — no sign-in needed:\n\n"
                    f"{per_person}\n\n"
                    f"Or open the whole team in one list: {one_click}\n\n"
                    f"For each person: what they did well, what they did not, and where to "
                    f"improve — plus confirm whether they hit their monthly target.\n"
                    f"Full screen on a computer: {BASE}/hris/monthly-feedback")
            if commit:
                OmniTask.objects.create(
                    assigner=assigner or mgr_user, assignee=mgr_user,
                    title=title[:200], body=body,
                    priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
                    due_at=due, source=tag)
                self._email_manager(mgr, mgr_user, owed, year, month, one_click)
            prompted += 1
            self.stdout.write(f"  → {mgr.full_name}: {len(owed)} owed")
        self.stdout.write(self.style.SUCCESS(
            f"Prompted {prompted} manager(s), skipped {skipped} already-prompted."
            + ('' if commit else '  [DRY RUN — pass --commit to write]')))

    # ── the private nudge with the one-click button ──────────────────────────
    def _email_manager(self, mgr, mgr_user, owed, year, month, one_click):
        """Email this manager their own feedback link. Best-effort — a mail
        failure must never stop the rest of the cycle."""
        from django.utils.html import escape
        to = (getattr(mgr, 'email', '') or getattr(mgr_user, 'email', '') or '').strip()
        if not to:
            return
        NAVY, ORANGE = '#0D1B2A', '#F4A623'
        period = dt.date(year, month, 1).strftime('%B %Y')
        rows = ''.join(
            f'<li style="margin:3px 0">{escape(p.employee.full_name)}</li>'
            for p in owed if p.employee)
        inner = f"""
          <h2 style="margin:0 0 6px;font-size:20px;color:{NAVY};">{escape(period)} feedback</h2>
          <p style="color:#6B7280;font-size:14px;margin:0 0 10px;">
            {len(owed)} of your team still need a short note from you. It takes a minute each.</p>
          <ul style="font-size:14px;color:#1F2937;padding-left:20px;margin:0 0 4px;">{rows}</ul>
          <p style="margin:20px 0 0;"><a href="{escape(one_click)}" style="display:block;
             text-align:center;background:{NAVY};color:#fff;text-decoration:none;padding:15px;
             border-radius:10px;font-weight:700;font-size:16px;">Give feedback now</a></p>
          <p style="text-align:center;color:#9CA3AF;font-size:12px;margin:8px 0 0;">
            Opens a secure page — no sign-in needed. Works on your phone.</p>"""
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F4F6;font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;">
  <div style="max-width:600px;margin:0 auto;padding:20px 12px;">
    <div style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">
      <div style="background:{NAVY};padding:20px 26px;">
        <div style="color:{ORANGE};font-size:18px;font-weight:700;">Alpha Direct · Monthly Feedback</div>
      </div>
      <div style="padding:22px 26px;">{inner}</div>
    </div>
    <p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:14px;">
      Omni ERP — omni.alphadirect.co.bw</p>
  </div>
</body></html>"""
        try:
            from core.notifications import send_html_with_cfo_cc
            send_html_with_cfo_cc(
                f'{period} performance feedback — {len(owed)} to do', html, [to],
                text_fallback=f'{len(owed)} of your team need {period} feedback: {one_click}',
                cc=None, cc_cfo=False)
        except Exception as exc:      # noqa: BLE001
            self.stderr.write(f'  ! feedback email to {to} failed: {exc}')
