"""
task_confirm_digest — twice-weekly nudge so managers CONFIRM finished work.

The task incentive + the Alpha League only count a finished task once a MANAGER
confirms it (a non-assignee leaves feedback — the CFO's anti-gaming rule). The
risk that rule creates: staff finish work, no manager clicks confirm, and nobody
earns. Fable flagged exactly this (2026-08-26). This digest closes the loop.

For each manager who has finished-but-unconfirmed tasks sitting in their court,
it emails them the list — each row with a ONE-TAP "Confirm done" link (signed,
no login, core.magic_action `task_confirm`). Tapping it writes the confirming
feedback and the task immediately counts. Anything waiting > 7 days is flagged.

Dry-run by default; --commit sends. Reuses the house HTML mailer (no CFO cc —
these are routine manager nudges). Runs Mon + Thu 07:00 (infra/cron).
    …/manage.py task_confirm_digest --commit
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone
from django.utils.html import escape

from core.models import OmniTask
from taskboard.cfo_views import _real_tasks

# Don't nag about ancient done tasks that will never be confirmed.
LOOKBACK_DAYS = 30
STALE_DAYS = 7                      # flag items waiting longer than this
NAVY, ORANGE = '#0D1B2A', '#F4A623'


class Command(BaseCommand):
    help = ('Email each manager their finished-but-unconfirmed tasks with one-tap '
            'confirm links. Dry-run unless --commit.')

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually send. Default = dry-run (prints who would get what).')

    def handle(self, *args, **opts):
        from core.magic_action import make_action_link
        from core.notifications import send_html_with_cfo_cc

        today = timezone.localdate()
        cutoff = today - timedelta(days=LOOKBACK_DAYS)

        # Finished real tasks the manager could confirm but hasn't: done, recent,
        # assigned to someone OTHER than the manager (can't self-confirm), with an
        # active assigner who has an email.
        qs = (_real_tasks(OmniTask.objects.filter(status=OmniTask.Status.DONE))
              .filter(completed_at__date__gte=cutoff)
              .exclude(assigner_id=F('assignee_id'))
              .filter(assigner__isnull=False, assigner__is_active=True)
              .select_related('assigner', 'assignee')
              .prefetch_related('feedback'))

        by_mgr: dict[int, dict] = {}
        for t in qs:
            # Unconfirmed = no NON-assignee feedback yet (Python over the prefetch).
            if any(f.from_user_id != t.assignee_id for f in t.feedback.all()):
                continue
            by_mgr.setdefault(t.assigner_id, {'mgr': t.assigner, 'tasks': []})['tasks'].append(t)

        sent = 0
        for grp in by_mgr.values():
            mgr, tasks = grp['mgr'], grp['tasks']
            if not (mgr.email or '').strip():
                self.stdout.write(f'  (skip {mgr.get_username()} — no email) {len(tasks)} unconfirmed')
                continue
            tasks.sort(key=lambda t: t.completed_at or timezone.now())
            oldest = max((today - (t.completed_at.date() if t.completed_at else today)).days for t in tasks)
            self.stdout.write(f'  {mgr.get_full_name() or mgr.get_username()}: '
                              f'{len(tasks)} to confirm (oldest {oldest}d)')
            if not opts['commit']:
                continue

            rows = []
            for t in tasks:
                who = (t.assignee.get_full_name() or t.assignee.username) if t.assignee_id else '—'
                days = (today - t.completed_at.date()).days if t.completed_at else 0
                stale = days >= STALE_DAYS
                link = make_action_link(mgr, 'task_confirm', task_id=str(t.id))
                rows.append(
                    f'<tr><td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">'
                    f'<b>{escape(t.title)}</b>'
                    f'<div style="color:#6B7280;font-size:12px;">{escape(who)} · finished {days} day(s) ago'
                    f'{" ⚠️ waiting a while" if stale else ""}</div></td>'
                    f'<td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;text-align:right;">'
                    f'<a href="{escape(link)}" style="background:{NAVY};color:#fff;text-decoration:none;'
                    f'font-size:13px;font-weight:bold;padding:8px 16px;border-radius:6px;">Confirm done</a>'
                    f'</td></tr>')

            html = (
                f'<p style="font-size:15px;color:#1F2A37;">You have {len(tasks)} finished task(s) waiting for '
                f'your OK. A tap confirms it — it then counts toward the person&rsquo;s Alpha League score and '
                f'monthly reward. Nothing you finished yourself is in this list.</p>'
                '<table style="border-collapse:collapse;width:100%;font-size:14px;margin-top:8px;">'
                + ''.join(rows) + '</table>'
                f'<p style="color:#6B7280;font-size:12px;margin-top:14px;">The buttons are personal to you and '
                f'expire in a few days — a fresh reminder brings new ones. Or open the board: '
                f'https://omni.alphadirect.co.bw/task-dashboard</p>')
            subject = f'{len(tasks)} finished task(s) to confirm — one tap each'
            try:
                send_html_with_cfo_cc(subject, html, [mgr.email], cc_cfo=False,
                                      text_fallback=f'{len(tasks)} finished task(s) await your confirmation. '
                                                    f'Open https://omni.alphadirect.co.bw/task-dashboard')
                sent += 1
            except Exception as e:                       # noqa: BLE001 — one bad send must not kill the rest
                self.stderr.write(f'  send failed for {mgr.email}: {e}')

        self.stdout.write(f'{"Sent" if opts["commit"] else "[dry-run] would notify"} '
                          f'{sent if opts["commit"] else len(by_mgr)} manager(s).')
